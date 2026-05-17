# Live Safety Checklist — Fortress Trading System
**Version:** 1.0 — 2026-05-17  
**Rule:** Do not advance to the next gate until the current gate is PASS.

---

## Gate 0 — Code Audit Complete

| | |
|-|-|
| **Status** | PASS |
| **Evidence** | `08_docs/live_readiness_audit.md` (2026-05-17) |
| **Command** | `type 08_docs\live_readiness_audit.md` |
| **Failure action** | Re-run audit if major files change |

---

## Gate 1 — No Hard-Coded Secrets

| | |
|-|-|
| **Status** | PASS |
| **Evidence** | All credential fields in `tick_tradovate_client.py` and `tick_live_executor.py` read from env vars or CLI args. No strings matching password/secret patterns in code. |
| **Command** | `grep -r "password\s*=\s*['\"][^'\"]\+" 04_codebase\` — should return zero hits |
| **Failure action** | Remove hardcoded value, rotate compromised credential immediately |

---

## Gate 2 — Dry-Run Executor Works

| | |
|-|-|
| **Status** | PASS |
| **Evidence** | `python tick_live_executor.py --quiet` prints DRY_RUN banner and signal alerts with no import errors |
| **Command** | `python tick_live_executor.py --quiet` |
| **Failure action** | Fix import error before proceeding |

---

## Gate 3 — REST Bar Builder Works

| | |
|-|-|
| **Status** | UNKNOWN |
| **Evidence** | Code written and imports clean, but not tested against live Tradovate credentials |
| **Command** | `python tick_bar_builder.py --rest --username $env:TRADOVATE_USERNAME --password $env:TRADOVATE_PASSWORD --cid $env:TRADOVATE_CID --secret $env:TRADOVATE_SECRET` |
| **Failure action** | Check credentials, confirm Tradovate demo account has market data access |
| **Pre-requisite** | Tradovate API credentials from Lucid Trading Settings → API Credentials |

---

## Gate 4 — WebSocket Bar Builder Works

| | |
|-|-|
| **Status** | UNKNOWN |
| **Evidence** | Not tested — requires live Tradovate credentials |
| **Command** | `python tick_bar_builder.py --username ... --password ... --cid ... --secret ...` |
| **Failure action** | Fall back to REST polling (`--rest`); investigate WebSocket auth error; check if API tier includes MD WebSocket |
| **Note** | Gate 3 (REST) is sufficient to proceed to Gate 5. WebSocket is preferred but optional. |

---

## Gate 5 — One Strategy Signal Replay Matches Backtest

| | |
|-|-|
| **Status** | UNKNOWN |
| **Evidence** | Not verified. Need to confirm signal on live bars matches what backtest would predict for same data. |
| **Command** | `python tick_live_executor.py --strategy 10 --quiet` — check that GC/trade_absorption_signal fires on known historical dates from backtest results |
| **Failure action** | Investigate signal function, confirm parameters match stress-test run |
| **How to verify** | Run executor on historical bars (not live), compare signal timestamps against `05_backtests/tick_results_v4_*.json` |

---

## Gate 6 — Bracket/OCO/OSO Order Support Exists

| | |
|-|-|
| **Status** | **FAIL** |
| **Evidence** | `TradovateClient.place_bracket_order()` does not exist. Executor confirms this at startup with BLOCKED message. |
| **Command** | `python tick_live_executor.py --demo-auto-trade --username x --password x` — should print BLOCKED |
| **Failure action** | Implement `place_bracket_order()` in `tick_tradovate_client.py`. Tradovate uses OSO (Order Sends Order) for bracket orders: POST `/order/placeOSO`. Test in Tradovate sandbox before enabling. |
| **Why critical** | Without broker-native stops, if the Python process crashes, your position has no stop loss at the exchange. This is a funded account. Do not bypass this gate. |

---

## Gate 7 — Broker State Reconciliation Exists

| | |
|-|-|
| **Status** | **FAIL** |
| **Evidence** | No reconciliation between `PositionTracker` (in-memory) and actual Tradovate positions. Restart loses all position state. |
| **Command** | N/A — feature does not exist |
| **Failure action** | Add startup reconciliation: call `tv_client.get_positions()` on executor start, populate `PositionTracker` from broker state, warn if discrepancy found. |
| **Why critical** | After any restart (crash, power outage, update), the executor would think it has no open positions and might enter duplicate positions. |

---

## Gate 8 — Kill Switch Tested

| | |
|-|-|
| **Status** | PASS (implemented, not yet live-tested) |
| **Evidence** | `tick_live_executor.py` checks `KILL_SWITCH.txt` at start of every pass. Creates `signals_*.jsonl` log. |
| **Command** | `echo STOP > C:\Users\conor\Desktop\quant-research\KILL_SWITCH.txt` while executor runs — it must exit within one poll cycle |
| **Failure action** | File is created, executor should stop on next pass. If it doesn't stop, investigate `_check_kill_switch()`. |
| **After test** | Delete KILL_SWITCH.txt to re-enable executor |

---

## Gate 9 — Demo Account Only, One Strategy, One Micro Contract

| | |
|-|-|
| **Status** | UNKNOWN — Gate 6 must pass first |
| **Evidence** | Requires `place_bracket_order()` implementation and Tradovate demo credentials |
| **Command** | `python tick_live_executor.py --poll 60 --strategy 2 --demo-auto-trade --username ... --password ... --cid ... --secret ...` |
| **Which strategy** | Start with Strategy #2: ES/cvd_divergence_large_print/15m (100% Topstep compliance, worst day $-3,827, 5.5 months tested) |
| **Why #2 first** | Lowest worst-day of ES strategies, all-hours except a few UTC avoid windows. Most conservative starter. |
| **Failure action** | Fix bracket order issue first (Gate 6). Confirm demo account has paper-trade enabled. |
| **Note** | Run for at least 1 full trading week before Gate 10 |

---

## Gate 10 — One Full Week Demo With Logs

| | |
|-|-|
| **Status** | NOT STARTED |
| **Evidence** | `06_live_trading/logs/` is empty |
| **Command** | Review `06_live_trading/logs/signals_*.jsonl` after 5 trading days |
| **What to check** | Signal count matches expectation, accepted/rejected ratio reasonable, no repeated identical signals, no duplicate orders, stop/target levels match backtest assumptions |
| **Failure action** | Investigate anomalies before proceeding to Gate 11 |

---

## Gate 11 — Post-Demo Slippage and Execution Report

| | |
|-|-|
| **Status** | NOT STARTED |
| **Evidence** | Requires completion of Gate 10 |
| **What to produce** | Compare actual fill prices vs. signal entry prices. Calculate actual slippage. Compare to backtest assumption (0.5–1.0 tick). Flag if actual > assumed. |
| **Failure action** | If actual slippage > 2 ticks consistently, demote strategies with thinner slippage tolerance (e.g., #11 ES/avg_order_size_divergence has 1t-Sharpe=1.03 — would fail at 2t) |

---

## Gate 12 — Manual Approval Before Any Live/Funded Account

| | |
|-|-|
| **Status** | NOT STARTED |
| **Who approves** | Conor (account owner) — explicit confirmation required |
| **Evidence required** | Gates 3–11 all PASS; at least 1 week of demo P&L reviewed; slippage report reviewed |
| **Command** | Set `FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND` only after manual review |
| **Failure action** | Do not proceed. The funded accounts have $1,000 remaining each. One bad live session without tested stops can wipe an account permanently. |

---

## Summary — Current Status

| Gate | Status |
|------|--------|
| Gate 0 — Audit | PASS |
| Gate 1 — No secrets | PASS |
| Gate 2 — Dry-run works | PASS |
| Gate 3 — REST bar builder | UNKNOWN (needs credentials) |
| Gate 4 — WebSocket bar builder | UNKNOWN |
| Gate 5 — Signal replay | UNKNOWN |
| Gate 6 — Bracket orders | **FAIL** |
| Gate 7 — Reconciliation | **FAIL** |
| Gate 8 — Kill switch | PASS (implemented) |
| Gate 9 — Single demo strategy | BLOCKED (Gate 6) |
| Gate 10 — 1 week demo | NOT STARTED |
| Gate 11 — Slippage report | NOT STARTED |
| Gate 12 — Manual approval | NOT STARTED |

**Current classification: PAPER READY — approaching DEMO READY**  
**Two hard gates remaining before demo: Gate 6 (bracket orders) and Gate 7 (reconciliation)**
