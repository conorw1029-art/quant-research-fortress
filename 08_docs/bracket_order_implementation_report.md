# Bracket Order Implementation Report — Gate 6
**Date:** 2026-05-17  
**Files changed:** `tick_tradovate_client.py`, `tick_live_executor.py`  
**Tests:** `test_tradovate_bracket_orders.py` (25 tests), `tick_dry_run_validation.py` (10 tests)

---

## 1. Is bracket order support fully implemented?

**YES — the interface is fully implemented with complete validation.**  
**NO — the actual API call has not been verified with real Tradovate credentials.**

`TradovateClient.place_bracket_order()` is now implemented in `tick_tradovate_client.py`. It:

- Validates all inputs (side, quantity, stop direction, target direction, risk limit)
- Builds a Tradovate `placeOSO` JSON payload (entry + OCO bracket)
- Defaults to `dry_run=True` — no API call unless explicitly opted out
- In demo mode with `dry_run=False`, calls `POST /order/placeOSO`
- Returns a structured `BracketOrderResult` dict on every code path

The executor's `_has_bracket_orders()` gate now returns `True`, meaning demo auto-trade is no longer blocked by the missing bracket method. It is still blocked by the credential requirement (no valid Tradovate credentials set).

---

## 2. Are stops/targets broker-native?

**YES — in demo/live mode they are broker-native via OSO.**  
**In dry-run mode, no orders are placed so there is nothing at the broker.**

When `place_bracket_order()` is called with `dry_run=False` and `demo=True`, it sends all three legs atomically to Tradovate via `POST /order/placeOSO`:

```
Entry order (market) 
  → on fill, Tradovate automatically submits:
    Target order (limit, GTC)  ← OCO with stop
    Stop order  (stop,  GTC)   ← OCO with target
```

If either the target or stop fills, Tradovate cancels the other leg. If the Python process crashes after the entry fills, both stop and target remain active at the exchange.

**This is what "broker-native" means — the stops and targets live at Tradovate, not in Python memory.**

---

## 3. Was any real API call made?

**NO.** All testing used:
- `dry_run=True` paths (no API call)
- Mocked `_post()` in unit tests

No credentials were provided. No live or demo Tradovate connection was established.

---

## 4. Did all mock tests pass?

**YES — 25/25 mock tests passed, 10/10 dry-run validation tests passed.**

| Test suite | Tests | Result |
|---|---|---|
| `test_tradovate_bracket_orders.py` | 25 | **25 PASS / 0 FAIL** |
| `tick_dry_run_validation.py` | 10 | **10 PASS / 0 FAIL** |

Mock tests covered:
- dry_run=True returns ok=True and makes no API call
- Invalid side rejected
- Invalid quantity rejected (zero, negative, over max)
- Stop on wrong side rejected (BUY/SELL)
- Target on wrong side rejected (BUY/SELL)
- Risk above $200 rejected
- Demo API failure returns ok=False
- Live mode blocked without FORTRESS_LIVE_ENABLE env var
- Live mode accepted with env var (mocked API)
- All result dicts contain required keys
- Auto-generated and custom client_order_id work correctly
- Executor's `_has_bracket_orders()` returns True

---

## 5. Is demo auto-trade now allowed or still blocked?

**Still blocked — one new blocker remains: Tradovate credentials.**

Gate status:

| Gate | Was | Now |
|------|-----|-----|
| Gate 6 — Bracket orders | **FAIL** (method missing) | **PASS** (method implemented, mock-verified) |
| Gate 7 — Reconciliation | FAIL | FAIL (unchanged) |
| Gate 9 — Single demo strategy | BLOCKED (Gate 6) | BLOCKED (Gate 7 + credentials) |

Demo auto-trade requires:
1. Gate 6 — PASS (done)
2. Gate 7 — Startup reconciliation (still missing)
3. Tradovate demo credentials in environment variables

Do not run demo auto-trade until Gate 7 is implemented and verified.

---

## 6. What exact command should I run next?

**Dry-run (safe to run right now — no broker connection needed):**
```powershell
cd C:\Users\conor\Desktop\quant-research\04_codebase
& "C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe" tick_live_executor.py `
  --poll 60 --quiet --alert-file alerts.json --max-runtime-minutes 30
```

**Re-run mock tests:**
```powershell
& "C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe" test_tradovate_bracket_orders.py
```

**Re-run full validation:**
```powershell
& "C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe" tick_dry_run_validation.py
```

**After getting Tradovate credentials — verify bracket orders with a single dry-run call:**
```powershell
$env:TRADOVATE_USERNAME = "your@email.com"
$env:TRADOVATE_PASSWORD = "yourpassword"
$env:TRADOVATE_CID      = "12345"
$env:TRADOVATE_SECRET   = "yoursecret"
& "C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe" tick_bar_builder.py --rest --username $env:TRADOVATE_USERNAME --password $env:TRADOVATE_PASSWORD --cid $env:TRADOVATE_CID --secret $env:TRADOVATE_SECRET
# Gate 3 (REST bar builder)
```

---

## 7. What remains unsafe?

### 1. OSO payload structure — needs live verification (Gate 6 partial)
The `placeOSO` JSON structure is implemented based on the Tradovate API reference, but has not been tested against a real demo account. The response parsing assumes a list of order confirmations `[entry, stop, target]`. If Tradovate returns a different structure, `place_bracket_order()` will return `ok=False` with `UNEXPECTED_RESPONSE` rather than silently accepting an invalid result.

**Do not treat Gate 6 as fully complete until at least one demo bracket order is placed and all three order IDs are returned in the response.**

### 2. Reconciliation not implemented (Gate 7 — FAIL)
On executor restart, `PositionTracker` is empty. If a position is open at the broker, the executor will re-enter and create a duplicate. This must be fixed before any demo auto-trade session.

### 3. Data freshness
The parquet bars end 2026-05-14. All signals are on stale data. Do not use signals for actual trading decisions until bar builder is connected to live data.

### 4. Contract month rollover
MESM5/MGCM5/MNQM5 expire approximately June 20, 2026. Update `TV_CONTRACT_MAP` in executor and `MICRO_SYMBOLS` in tradovate client before that date.

---

## Summary

Gate 6 is **structurally implemented and mock-verified**. The `place_bracket_order()` method exists, validates all inputs, builds broker-native OSO payloads, and is correctly integrated into the executor. Both test suites pass.

Gate 6 is **not yet exchange-verified**. One demo trade with real credentials is required to confirm the OSO payload structure and response parsing before Gate 9 can be opened.

**Next task: Gate 7 — Startup reconciliation in tick_live_executor.py.**
