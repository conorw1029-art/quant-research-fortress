# No Demo Account Execution Plan

**Status:** ENFORCED — All broker connections blocked until a Tradovate demo/sim account exists.  
**Last updated:** 2026-06-02  
**System classification:** DATA_READY | BROKER_MOCK_ONLY | DRY_RUN_READY | NOT_DEMO_READY | NOT_LIVE_READY

---

## Why This Document Exists

I have real/funded Tradovate accounts but no demo/simulation account. Until a true demo/sim account
is available, the system must operate in mock-only mode. This document records the exact restrictions
and the path to lifting them safely.

---

## Current Execution Constraints

| Capability | Status | Reason |
|------------|--------|--------|
| Databento data pull (local key) | **ALLOWED** | Read-only, no risk, key present |
| Strategy backtesting (offline) | **ALLOWED** | Uses stored parquet files only |
| Evidence upgrade framework | **ALLOWED** | Runs on historical data only |
| Dry-run signal simulation | **ALLOWED** | No orders placed |
| Mock broker simulation | **ALLOWED** | `tick_mock_broker.py`, no API calls |
| Tradovate demo auto-trade | **BLOCKED** | No demo account exists |
| Tradovate funded auto-trade | **BLOCKED** | Would risk funded capital |
| Bracket order live testing | **BLOCKED** | Requires demo account first |
| OSO/OCO exchange-verified testing | **BLOCKED** | Requires demo account first |
| Any Tradovate API connection | **BLOCKED** | `TRADOVATE_ENABLED=false` enforced |

---

## Environment Variables That Enforce This

```
EXECUTION_MODE=DRY_RUN
BROKER_MODE=MOCK_ONLY
TRADOVATE_ENABLED=false
TRADOVATE_ENV=none

# These must remain blank until a demo account is created
TRADOVATE_USERNAME=
TRADOVATE_PASSWORD=
TRADOVATE_CID=
TRADOVATE_SECRET=

# NEVER set this flag
# FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND
```

Any script that reads `TRADOVATE_ENABLED=false` must skip all broker calls cleanly.
Any script that detects `FORTRESS_LIVE_ENABLE` is set must fail immediately (as a safety gate).

---

## What "MOCK_ONLY" Means Operationally

1. **Strategy signals are generated** from live or historical bar data.
2. **Signals are logged** to alert files, not sent to any broker.
3. **The mock broker** (`tick_mock_broker.py`) can simulate fills, positions, and bracket orders
   against those signals for end-to-end dry-run testing.
4. **No API calls** are made to Tradovate or any exchange.
5. **No capital** is at risk.

---

## Path to Enabling Demo Auto-Trade

When a Tradovate paper-trading/simulation account is created:

1. Set credentials in `.env`:
   ```
   TRADOVATE_ENABLED=true
   TRADOVATE_ENV=demo
   TRADOVATE_USERNAME=your@email.com
   TRADOVATE_PASSWORD=yourpassword
   TRADOVATE_CID=your_cid
   TRADOVATE_SECRET=your_secret
   ```

2. Run credential preflight to verify:
   ```
   venv_new\Scripts\python.exe -X utf8 04_codebase/tick_credentials_preflight.py
   ```
   Expected: `TRADOVATE_CREDENTIALS_PRESENT` classification.

3. Test mock broker reconciliation against demo API (read-only position queries first).

4. Run bracket order integration tests on demo account.

5. Verify OSO/OCO order routing works correctly.

6. Only after 5 consecutive trading days of clean demo fills: consider enabling auto-trade.

**Funded accounts** are never used for testing. Demo must be fully validated first.

---

## Bracket Order Testing Sequence (Once Demo Exists)

Must be done in order — no skipping:

1. Market order → demo account receives order
2. Limit order → demo account receives order
3. Stop order → demo account receives order
4. Bracket (OSO): entry + stop + target → all three legs received
5. Partial fill handling
6. OCO cancels sibling on fill
7. Position reconciliation after fills
8. Forced liquidation test
9. Disconnect/reconnect with open position test
10. Slippage measurement vs expected

---

## Mock Broker Tests (Available Now, No Demo Required)

The mock broker at `04_codebase/tick_mock_broker.py` supports:

- Account state (balance, equity, margin)
- Position tracking (long/short/flat)
- Order placement (market, limit, stop)
- Bracket order simulation (OSO/OCO)
- Fill simulation (instant, slippage-adjusted)
- Order rejection scenarios
- Broker disconnect simulation
- Missing stop/target scenarios
- Reconciliation mismatch detection

Run mock broker smoke test:
```
venv_new\Scripts\python.exe -X utf8 04_codebase/tick_mock_broker.py
```

---

## What Must NOT Happen Until Demo Exists

- Do NOT use funded Tradovate credentials in any test script
- Do NOT connect to Tradovate with `TRADOVATE_ENABLED=true` in `.env`
- Do NOT enable `--auto-trade` flag in live executor
- Do NOT place real orders of any kind
- Do NOT log in to Tradovate programmatically
- Do NOT run `tick_live_executor.py` in auto-trade mode
- Do NOT set `FORTRESS_LIVE_ENABLE`

---

## Current Safe Workflow

```
# Step 1: Verify environment is safe
venv_new\Scripts\python.exe -X utf8 04_codebase/tick_credentials_preflight.py

# Step 2: Run strategy signals in dry-run (no orders)
venv_new\Scripts\python.exe -X utf8 04_codebase/tick_live_executor.py

# Step 3: Test order logic against mock broker
venv_new\Scripts\python.exe -X utf8 04_codebase/tick_mock_broker.py

# Step 4: Evidence upgrade on backtest survivors
venv_new\Scripts\python.exe -X utf8 04_codebase/tick_evidence_upgrade.py \
  --survivors 05_backtests/l2_results/GC_quick_hardened_survivors.json \
  --bars 01_data/tick_bars/GC_bars_1m.parquet
```

---

## Verification Checklist (Run Before Any New Session)

- [ ] `TRADOVATE_ENABLED=false` in `.env`
- [ ] `BROKER_MODE=MOCK_ONLY` in `.env`  
- [ ] `EXECUTION_MODE=DRY_RUN` in `.env`
- [ ] `FORTRESS_LIVE_ENABLE` is NOT set
- [ ] Credential preflight shows `TRADOVATE_DISABLED`
- [ ] No Python processes connecting to `tradovate.com`
- [ ] `.env` not committed to git
