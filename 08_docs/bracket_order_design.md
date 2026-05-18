# Bracket Order / OCO / OSO Implementation Plan
**Date:** 2026-05-18  
**Audit target:** `04_codebase/tick_tradovate_client.py`  
**Status:** DESIGN DOCUMENT — Do not demo-trade until all gaps resolved

---

## 1. What Currently Exists in tick_tradovate_client.py

### Implemented and Working (dry-run verified)
| Feature | Method | Notes |
|---|---|---|
| Authentication (demo) | `authenticate()` | Obtains access token; demo URL is `https://demo.tradovateapi.com/v1` |
| Authentication (live) | `authenticate()` | Live URL blocked unless `FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND` env var set |
| Demo / live URL separation | `_base_url` property | Clean separation; live guard raises `RuntimeError` without env var |
| Account lookup | `_resolve_account_id()` | Fetches account list, returns first account ID |
| Contract lookup | `get_contract_id(symbol)` | Calls `/contract/find` — finds active contract by symbol string |
| Market order | `place_order()` | Supports Market, Limit, Stop; dry_run=True default |
| Limit order | `place_order()` | As above |
| Stop order | `place_order()` | As above |
| Cancel order | `cancel_order(order_id)` | Calls `/order/cancel` |
| Flatten position | `flatten_position(symbol)` | Calls `/order/liquidatePosition` |
| Order status | `get_order_status(order_id)` | Calls `/order/item` |
| Position lookup | `get_positions_dict()` | Returns `{symbol: {"netPos": int, ...}}` |
| Quote lookup | `get_quote(symbol)` | Calls `/md/getQuote` |
| Account info | `get_account_info()` | Returns balance, margin, etc. |
| Bracket order (OSO) | `place_bracket_order()` | Implemented via `/order/placeOSO`; dry_run=True default, demo=True default |

### Gaps Identified in Audit

| Gap | Severity | Detail |
|---|---|---|
| OSO payload structure unverified | **CRITICAL** | `_build_oso_payload()` sends `{"first": entry, "second": {"orders": [stop, target]}}`. This format has NOT been verified against Tradovate's actual OSO endpoint response. The exact bracket structure may differ from spec. |
| Response parsing assumes list format | **CRITICAL** | `_parse_oso_response()` assumes response is a list `[entry_conf, stop_conf, target_conf]`. Real Tradovate OSO response may be a dict with nested order confirmations — unverified. |
| No kill switch check inside `place_bracket_order()` | **HIGH** | The function does not read `KILL_SWITCH.txt` before submitting to the API. If the kill switch is set after the strategy fires but before order submission, the order still goes through. |
| No session-open check | **HIGH** | No verification that the exchange session is currently open before placing an order. Orders can be submitted in pre-market or after-hours when market is halted. |
| `oco_id` / `oso_id` missing from result | **MEDIUM** | The `BracketOrderResult` dict does not include `oco_id` or `oso_id`. If a partial fill occurs and only the stop or target leg is active, the system has no way to identify the OCO group to cancel the remaining leg. |
| No tick-rounding validation | **MEDIUM** | Entry, stop, and target prices are not validated against the instrument's minimum tick size before submission. An off-tick price will be rejected by the exchange. |
| No stop-side validation | **MEDIUM** | For BUY: stop must be below entry, target must be above. This is not validated in code. An inverted bracket (stop above entry for a long) would result in immediate stop trigger. |
| No dollar-risk validation | **MEDIUM** | No check that `(entry - stop) * tick_value * quantity <= $200` before submission. |
| No allowlist check inside function | **LOW** | `place_bracket_order()` does not verify the strategy is on the allowlist. The executor checks this upstream, but defence-in-depth requires the function to also check. |

---

## 2. Required Future Interface

```python
def place_bracket_order(
    symbol: str,
    side: str,                        # "BUY" or "SELL"
    quantity: int,                    # Must be >= 1
    entry_type: str,                  # "Market" or "Limit"
    entry_price: Optional[float],     # Required if entry_type == "Limit"
    stop_price: float,                # Broker-native stop loss price
    target_price: float,              # Broker-native target price
    account_id: Optional[str] = None, # Defaults to resolved account
    demo: bool = True,                # Always True until explicitly cleared
    client_order_id: Optional[str] = None,  # For duplicate detection
    dry_run: bool = True,             # Always True by default
) -> dict:
    """
    Returns:
    {
        "ok": bool,
        "mode": "DRY_RUN" | "DEMO" | "LIVE_BLOCKED",
        "entry_order_id": str | None,
        "stop_order_id": str | None,
        "target_order_id": str | None,
        "oco_id": str | None,          # OCO group ID linking stop + target
        "oso_id": str | None,          # OSO group ID linking entry + OCO
        "client_order_id": str,
        "reason": str,                 # Populated on failure
        "payload": dict,               # The exact payload sent (always logged)
    }
    """
```

---

## 3. Safety Requirements (All Must Pass Before Submission)

Each check must fail fast with a structured reason string.

### Pre-Submission Validations

| # | Check | Failure Mode | Reason String |
|---|---|---|---|
| 1 | `dry_run=True` by default | Block live submission | `"DRY_RUN_DEFAULT"` |
| 2 | `demo=True` by default | Block live submission | `"DEMO_DEFAULT"` |
| 3 | `FORTRESS_LIVE_ENABLE` not set | Block live URL | `"LIVE_BLOCKED_ENV_NOT_SET"` |
| 4 | Kill switch not STOP | Block all submission | `"KILL_SWITCH_STOP"` |
| 5 | Session is open | Block if market closed | `"SESSION_CLOSED"` |
| 6 | `side` in {"BUY", "SELL"} | Reject invalid | `"INVALID_SIDE"` |
| 7 | `symbol` resolvable to contract ID | Reject unknown symbol | `"SYMBOL_NOT_FOUND"` |
| 8 | `quantity >= 1` | Reject zero/negative | `"INVALID_QUANTITY"` |
| 9 | `quantity <= max_contracts` (currently 1) | Reject oversized | `"QUANTITY_EXCEEDS_MAX"` |
| 10 | For BUY: `stop_price < entry_price` | Reject inverted bracket | `"STOP_ABOVE_ENTRY_FOR_BUY"` |
| 11 | For BUY: `target_price > entry_price` | Reject inverted bracket | `"TARGET_BELOW_ENTRY_FOR_BUY"` |
| 12 | For SELL: `stop_price > entry_price` | Reject inverted bracket | `"STOP_BELOW_ENTRY_FOR_SELL"` |
| 13 | For SELL: `target_price < entry_price` | Reject inverted bracket | `"TARGET_ABOVE_ENTRY_FOR_SELL"` |
| 14 | `abs(entry - stop) > 0` (ticks) | Reject zero-distance stop | `"ZERO_STOP_DISTANCE"` |
| 15 | `abs(entry - target) > 0` (ticks) | Reject zero-distance target | `"ZERO_TARGET_DISTANCE"` |
| 16 | Prices are tick-rounded | Reject off-tick | `"OFF_TICK_PRICE"` |
| 17 | Dollar risk `<= $200` | Reject oversized | `"ESTIMATED_RISK_EXCEEDS_LIMIT"` |
| 18 | No naked market entry (always use bracket) | Reject naked entry | `"NAKED_ENTRY_NOT_ALLOWED"` |
| 19 | Bracket support proven or dry_run=True | Block demo if unproven | `"BRACKET_NOT_SUPPORTED_YET"` |

### Post-Submission Requirements
- Log the exact payload sent regardless of outcome
- Log the full raw response from Tradovate regardless of outcome
- Verify response contains entry order ID
- Verify response contains stop order ID
- Verify response contains target order ID
- Verify response contains OCO group ID
- If any verification fails: log CRITICAL, return `ok=False`, do not assume protection exists

---

## 4. Structured Result Format

```python
{
    "ok": True | False,
    "mode": "DRY_RUN" | "DEMO" | "LIVE_BLOCKED",
    "entry_order_id": "12345" | None,
    "stop_order_id": "12346" | None,
    "target_order_id": "12347" | None,
    "oco_id": "oco_98765" | None,    # OCO ID linking stop + target as pair
    "oso_id": "oso_11111" | None,    # OSO ID linking entry + OCO bracket
    "client_order_id": "strat2_20260518_143001",
    "reason": "",                    # Non-empty only on failure
    "payload": {                     # Exact payload submitted (logged always)
        "accountSpec": "...",
        "accountId": 12345,
        "action": "Buy",
        "symbol": "MESM5",
        "orderQty": 1,
        "orderType": "Limit",
        "price": 5320.25,
        ...
    }
}
```

---

## 5. Design Decision: Tradovate OSO Implementation

### What Tradovate Provides
Tradovate supports OSO (One-Sends-Other) orders via `/order/placeOSO`. An OSO sends a primary entry order, and on fill, automatically activates a secondary OCO bracket (stop + target as an either/or pair).

The intended structure:
```json
{
  "first": {
    "accountSpec": "...",
    "accountId": 123,
    "action": "Buy",
    "symbol": "MESM5",
    "orderQty": 1,
    "orderType": "Limit",
    "price": 5320.25,
    "timeInForce": "DAY"
  },
  "second": {
    "orders": [
      {
        "action": "Sell",
        "symbol": "MESM5",
        "orderQty": 1,
        "orderType": "Limit",
        "price": 5340.00,
        "timeInForce": "GTC"
      },
      {
        "action": "Sell",
        "symbol": "MESM5",
        "orderQty": 1,
        "orderType": "Stop",
        "stopPrice": 5310.00,
        "timeInForce": "GTC"
      }
    ]
  }
}
```

### Unverified Elements
1. Whether `"second"` wraps in `"orders"` array or directly contains `"target"` and `"stop"` fields
2. Whether the response structure is `[entry_conf, stop_conf, target_conf]` list or a nested dict
3. Whether `oco_id` and `oso_id` are returned in the response and their exact field names
4. Whether GTC time-in-force is supported for the bracket legs or requires `DAY`
5. Whether the stop leg requires `stopPrice` or `price` field name

### Recommended Mock Test Before Exchange Verification
Build a mock Tradovate server (or use recorded fixtures) that:
1. Returns a known OSO response shape for a dry-run order
2. Verifies the parser correctly extracts all 5 IDs (entry, stop, target, oco, oso)
3. Tests the 19 validation rules with boundary cases
4. Tests kill-switch blocking mid-flight
5. Tests session-closed blocking

---

## 6. Explicit Conclusion

**The system must remain NOT DEMO AUTO-TRADE READY until:**

1. OSO payload structure is verified against Tradovate's actual API (not just assumed correct)
2. Response parsing is verified to correctly extract all order IDs including OCO/OSO group IDs
3. All 19 safety validations are implemented and tested
4. Kill switch check is added inside `place_bracket_order()`
5. Session-open gate is implemented
6. State persistence is built so active bracket IDs survive restart
7. Broker reconciliation is built to detect missing brackets after reconnect

**If broker-native bracket orders cannot be verified, return `ok=False, reason="BRACKET_NOT_SUPPORTED_YET"` and keep demo auto-trade blocked.**

Do not fake bracket orders by using internally-tracked stops only. If the process crashes with an open position and no broker-native stop, capital is unprotected until manual intervention.

---

*Next action: Write mock bracket order test suite. Do not connect to demo API until mock tests pass.*
