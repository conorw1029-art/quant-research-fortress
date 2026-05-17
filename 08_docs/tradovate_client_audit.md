# Tradovate Client Audit — tick_tradovate_client.py
**Date:** 2026-05-17  
**File:** `04_codebase/tick_tradovate_client.py`

---

## Classification: MARKET_ORDER_ONLY

The current client can authenticate and place simple market orders. It has no bracket/OCO/OSO support and no order cancellation. Auto-trading is blocked in the executor until bracket orders are implemented.

---

## Capability Inventory

| Capability | Status | Endpoint / Note |
|---|---|---|
| Authentication | YES | `POST /auth/accesstokenrequest` — OAuth2 access token, auto-refresh |
| Endpoint selection | YES | Demo URL / Live URL driven by `demo=True/False` constructor arg |
| Account lookup | YES | `GET /account/list` — stores first account's ID |
| Account balance | YES | `GET /cashbalance/getcashbalancesnapshot` |
| Contract lookup | YES | `GET /contract/suggest` — resolves symbol → contract ID |
| Position lookup | YES | `GET /position/list` — filters zero-position records |
| Market orders | YES | `POST /order/placeorder` with `orderType=Market` |
| Limit orders | PARTIAL | `price` field in TradovateOrder exists; not tested end-to-end |
| Stop orders | PARTIAL | `stop_price` field in TradovateOrder exists; not tested end-to-end |
| Order status lookup | NO | No `GET /order/item` or fill-status polling |
| Cancel order | NO | No cancel endpoint call |
| Flatten position | YES | `close_position()` — places opposing market order |
| Close all positions | YES | `close_all_positions()` — loops positions, closes each |
| OCO orders | NO | `/order/placeOCO` not implemented |
| OSO / bracket orders | NO | `/order/placeOSO` not implemented |
| Live price quotes | YES | `GET /md/getquotesnapshot` |

---

## What Is Missing for Safe Auto-Trading

### 1. Bracket / OSO Orders (Gate 6 — FAIL)
Without broker-native stops, if the Python process crashes during a trade, the position has no stop loss at the exchange. This is the primary safety blocker.

The current client places naked market entries only. When the Python process closes or crashes, the position is unprotected.

**Required:** `place_bracket_order()` using Tradovate's `/order/placeOSO` endpoint, which atomically sends:
- Entry order (market or limit)
- Stop-loss order (becomes active on entry fill)
- Target order (OCO pair with stop, becomes active on entry fill)

### 2. Order Status Polling (Gate 7 dependency)
After placing an entry, the executor needs to confirm fill status before managing the position. Currently there is no `get_order_status()` method.

### 3. Account State Reconciliation (Gate 7 — FAIL)
On restart, the executor does not query Tradovate for open positions. If the process restarts mid-trade, the risk manager will re-enter positions that are already open at the broker.

---

## Why Not BASIC_DEMO_EXECUTION or Higher

The client can technically place market orders in demo mode, but:
- No stops attached → position is naked on entry
- No reconciliation → restarts create duplicate positions
- No fill confirmation → executor doesn't know if orders were accepted

These gaps make even demo execution unsafe. The executor's bracket-order gate correctly blocks auto-trade until these are resolved.

---

## After Gate 6 Implementation

Once `place_bracket_order()` is added and verified, classification becomes:

**BRACKET_CAPABLE_DEMO** — pending live verification with real demo credentials.

The method must:
- Send all three legs atomically via `/order/placeOSO`
- Never return `ok=True` in demo mode without a confirmed broker response
- Default to `dry_run=True` so no API calls are made unless explicitly requested
