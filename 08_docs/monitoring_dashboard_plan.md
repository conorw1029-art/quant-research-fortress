# Monitoring Dashboard Plan

**Status**: Planning  
**Priority**: status.json and heartbeat.json this week; Telegram when credentials arrive.

---

## 1. Required Status Fields

The dashboard must expose all of the following at all times. These are not optional — if a field is missing, the dashboard is incomplete.

### System State
- Current mode: `DRY_RUN` / `DEMO` / `LIVE_BLOCKED` (no bare "LIVE" without an explicit safety review)
- Kill switch status: armed / triggered
- Broker connection status: connected / disconnected / error
- Heartbeat age: seconds since last executor pass
- Process uptime in seconds
- Log file paths (where to find executor logs, risk logs, AI decision logs)

### Strategies
- Per-strategy status: `ACTIVE` / `HALTED` / `DISABLED_FOR_LIVE` / `RESEARCH`
- Per-strategy current position (contracts, direction)
- Per-strategy today P&L
- Per-strategy halt flag and halt reason
- Open orders and active brackets (order ID, type, price, side)

### Risk
- Portfolio open positions (total contracts across all strategies)
- Portfolio daily P&L (unrealised + realised)
- Daily loss limit remaining (in dollars)
- Trailing drawdown remaining (in dollars)

### Data Quality
- Per-symbol: last bar timestamp, age in minutes
- Stale data flag if age > threshold during market hours

### Signals
- Latest signal per strategy: direction, time, action taken (filled / skipped / no fill)
- Rejection reasons for skipped signals (risk limit, not in session, kill switch, allowlist, etc.)

### Calendar
- Upcoming high-impact news events (FOMC, CPI, NFP) — next 24 hours
- Contract rollover warning (if within 5 days of front-month expiry)

---

## 2. Implementation Options

| Option | Description | Pros | Cons |
|--------|------------|------|------|
| Option 1: Local HTML dashboard | Python writes status.json; JS reads and renders it in a browser | Most visual; easy to read at a glance | Requires browser open; more build work |
| Option 2: JSON status file | Executor writes status.json every pass; human reads it directly or in any viewer | Simplest possible; works with any tool; no UI code | Not as scannable as visual dashboard |
| Option 3: Telegram bot alerts | Push alerts for critical events to Telegram | Works on phone; immediate notification | Not a full dashboard; no persistent state view |
| Option 4: Discord webhooks | Push alerts to a Discord channel | Easy to set up; searchable history | Same limitation as Telegram |

These options are not mutually exclusive. The recommended path layers them progressively.

---

## 3. Recommended First Version

**This week**:
1. Write `06_live_trading/state/status.json` on every executor pass (every loop iteration)
2. Write `06_live_trading/state/heartbeat.json` on every executor pass
3. Telegram alerts for the 10 critical events (see Section 6) — implement as soon as Telegram credentials are available

**Later** (after demo trading begins):
- Simple HTML dashboard reading status.json and rendering it (static file, opens in browser, no server needed)
- Expand Telegram bot to support `/status` query (read-only, returns current status.json summary)

**Not now**:
- Discord (Telegram is sufficient for Phase 1)
- Real-time websocket dashboard (unnecessary complexity before demo)
- Mobile app (unnecessary)

---

## 4. status.json Schema

Written to `06_live_trading/state/status.json` on every executor pass. Overwrite in place; do not append.

```json
{
  "timestamp": "2026-05-18T14:32:00Z",
  "mode": "DRY_RUN",
  "strategies": {
    "1": {
      "name": "vwap_reclaim_gc",
      "status": "DISABLED_FOR_LIVE",
      "position": 0,
      "position_direction": null,
      "today_pnl": 0.0,
      "halt": false,
      "halt_reason": null,
      "last_signal": {
        "time": "2026-05-18T13:45:00Z",
        "direction": "LONG",
        "action": "SKIPPED",
        "reason": "DISABLED_FOR_LIVE"
      }
    },
    "2": {
      "name": "si_momentum_reversion",
      "status": "DISABLED_FOR_LIVE",
      "position": 0,
      "position_direction": null,
      "today_pnl": 0.0,
      "halt": false,
      "halt_reason": null,
      "last_signal": null
    }
  },
  "portfolio": {
    "open_positions": 0,
    "open_orders": [],
    "active_brackets": [],
    "daily_pnl": 0.0,
    "daily_loss_remaining": 600.0,
    "trailing_dd_remaining": 800.0
  },
  "data": {
    "GC_1m": {
      "last_bar": "2026-05-18T14:31:00Z",
      "age_min": 1,
      "stale": false
    },
    "ES_15m": {
      "last_bar": "2026-05-18T14:30:00Z",
      "age_min": 2,
      "stale": false
    },
    "NQ_15m": {
      "last_bar": "2026-05-18T14:30:00Z",
      "age_min": 2,
      "stale": false
    }
  },
  "system": {
    "kill_switch": false,
    "kill_switch_reason": null,
    "broker_connected": false,
    "heartbeat_age_s": 30,
    "process_uptime_s": 3600,
    "log_paths": {
      "executor": "06_live_trading/logs/executor.log",
      "risk": "06_live_trading/logs/risk_events.jsonl",
      "ai_decisions": "06_live_trading/logs/ai_decisions.jsonl",
      "orders": "06_live_trading/logs/orders.jsonl"
    }
  },
  "upcoming_news": [
    {
      "event": "FOMC Rate Decision",
      "time": "2026-05-20T18:00:00Z",
      "impact": "HIGH"
    }
  ],
  "rollover_warning": null,
  "errors": []
}
```

### Field Notes

| Field | Notes |
|-------|-------|
| `mode` | Only `DRY_RUN`, `DEMO`, or `LIVE_BLOCKED`. Never a bare `LIVE` without audit. |
| `strategies[n].status` | `ACTIVE`, `HALTED`, `DISABLED_FOR_LIVE`, or `RESEARCH` |
| `portfolio.open_orders` | Array of `{order_id, strategy_id, side, quantity, order_type, price}` |
| `portfolio.active_brackets` | Array of `{entry_order_id, stop_order_id, target_order_id, strategy_id}` |
| `data[sym].stale` | True if age_min exceeds threshold during RTH hours (default: 60 min) |
| `system.kill_switch` | True means all trading halted; must show prominently in any visual dashboard |
| `errors` | Array of `{time, source, message}` for any ERROR/CRITICAL log entries since last pass |

---

## 5. heartbeat.json Schema

Written to `06_live_trading/state/heartbeat.json` on every executor pass. Simpler than status.json — used by the Monitor Agent to detect process death.

```json
{
  "timestamp": "2026-05-18T14:32:00Z",
  "pid": 12345,
  "uptime_s": 3600,
  "last_pass_ms": 45
}
```

| Field | Description |
|-------|------------|
| `timestamp` | ISO8601 UTC timestamp of last heartbeat write |
| `pid` | OS process ID of the executor process |
| `uptime_s` | Seconds since executor process started |
| `last_pass_ms` | Duration in milliseconds of the last executor loop pass |

**Monitor Agent alert rule**: if `heartbeat.json` has not been updated in > 2x the expected loop interval, alert immediately. If the file is more than 5 minutes stale during market hours, treat as process death.

---

## 6. Critical Alert Triggers

The following 10 events must generate an **immediate** alert to Telegram (and any other configured channel). No batching; no delay; no waiting for EOD report.

| # | Event | Trigger Condition |
|---|-------|------------------|
| 1 | Kill switch STOP | `kill_switch` transitions to `true` for any reason |
| 2 | Broker position mismatch | Broker-reported position != internal tracked position for any strategy |
| 3 | Missing bracket order | Entry filled but stop or target order not confirmed within N seconds |
| 4 | Data stale during market hours | Any symbol's last bar age > 60 minutes during RTH session |
| 5 | Strategy halted by circuit breaker | Any strategy `halt` flag transitions to `true` |
| 6 | Daily loss limit > 80% consumed | `daily_loss_remaining` drops below 20% of starting limit |
| 7 | Account halt triggered | Broker or prop firm imposes an account-level trading halt |
| 8 | Order rejected by broker | Broker returns an order rejection for any submitted order |
| 9 | API disconnected | Broker API connection drops; reconnection attempt initiated |
| 10 | Unexpected broker position | Broker reports an open position that the system has no record of creating |

For each alert, the message must include: timestamp, event type, severity (`CRITICAL` or `WARNING`), relevant values (e.g., which symbol is stale, which strategy was halted, what the position mismatch is).

---

## 7. Telegram Bot Design

### Architecture

- Simple Python bot using the Telegram Bot API
- Bot token stored in environment variable `TELEGRAM_BOT_TOKEN`
- Chat ID stored in environment variable `TELEGRAM_CHAT_ID`
- Alert Bot agent calls a single function: `send_alert(event_dict)` which formats and delivers the message

### Message Format

```
[CRITICAL] Kill Switch Triggered
Time: 2026-05-18 14:32:00 UTC
Reason: Daily loss limit exceeded
Action required: Review logs, manually assess positions
```

```
[WARNING] Daily Loss Limit 83% Consumed
Time: 2026-05-18 14:15:00 UTC
Daily P&L: -$498 of -$600 limit
Remaining: $102
Strategy: vwap_reclaim_gc (3 consecutive losses)
```

### Design Rules

- One Telegram message per event. No batching multiple events into one message.
- Bot is **read-only** in Phase 1. It cannot receive commands that modify the system.
- Messages are append-only in the Telegram chat history — full audit trail.
- Later upgrade: `/status` command returns a formatted snapshot of current status.json (read-only).
- Never implement commands that enable strategies, disable kill switch, or change any system state via Telegram.

### Non-Critical Events (EOD Summary Only)

These go into the daily EOD summary message, not immediate alerts:
- Individual signals fired
- Individual trades completed
- Skipped trades (non-critical)
- Slippage variance
- Data age > 30 min but < 60 min

---

## 8. Implementation Priority

| Deliverable | Priority | When |
|------------|----------|------|
| `status.json` written on every executor pass | Highest | This week |
| `heartbeat.json` written on every executor pass | Highest | This week (same pass as status.json) |
| Telegram bot token + chat ID configured | High | When credentials available |
| 10 critical alert triggers implemented | High | Alongside Telegram setup |
| EOD daily summary message | Medium | After demo trading begins |
| HTML dashboard (browser view of status.json) | Low | After demo trading proves stable |
| `/status` Telegram query command | Low | After core bot is proven |
| Discord webhook mirror | Optional | Only if Telegram proves insufficient |
