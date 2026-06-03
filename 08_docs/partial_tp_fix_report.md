# Partial TP Fix Report
**Date:** 2026-06-03  
**Status:** COMPLETE  
**Affected file:** `04_codebase/tick_risk_manager.py`

---

## Problem

The `tick_risk_manager.py` `RiskManager.update_bar()` method contained a legacy partial
exit path (section 2b — "Legacy partial exit, 2+ contract mode") that fired regardless of
how many contracts were in the trade.

When `contracts == 1`, calling `_do_partial()` attempts to close `fraction = 0.5` of the
position, which translates to 0.5 contracts — a quantity that no futures exchange accepts.
The fractional fill would either be rejected by the broker or, in the mock broker, produce
an inconsistent internal state where the position shows 0.5 contracts held.

### Root cause

The guard in `RiskConfig` (`use_ratchet: bool = True`) only switches the *path* taken in
section 2a vs 2b. When `use_ratchet=False`, the code reached section 2b without checking
whether `trade.contracts >= 2`. The partial exit fired on every 1-contract trade that passed
the `partial_exit_r` price level.

### Impact scope

- **Backtests**: no impact — all L2 backtests use the direct exit scanner (`_scan_exit` in
  `l2_ofi_strategies.py`) which does not call `RiskManager.update_bar()`.
- **Live / dry-run via `tick_live_executor.py`**: affected only when `use_ratchet=False`
  in the passed `RiskConfig`. The default is `use_ratchet=True`, so live runs with default
  config were not affected in practice.
- **`tick_risk_manager.py` unit tests**: would surface the issue if `use_ratchet=False`
  and `contracts=1` are combined.

---

## Fix Applied

**File:** `04_codebase/tick_risk_manager.py`  
**Line:** section 2b inside `RiskManager.update_bar()`

**Before:**
```python
# ── 2b. Legacy partial exit (2+ contract mode) ────────────────────
elif not trade.partial_done:
```

**After:**
```python
# ── 2b. Legacy partial exit (2+ contract mode only) ───────────────
# Partial exit splits 50% of the position — impossible with 1 contract.
# Guard ensures we never attempt fractional-contract exits.
elif not trade.partial_done and trade.contracts >= 2:
```

The change is a single conjunction. When `contracts == 1`, section 2b is skipped
entirely. The trade falls through to sections 3 (full target) and 4 (time stop), which
are safe for any quantity ≥ 1. `_do_close()` uses `remaining = 0.5 if trade.partial_done
else 1.0` — since `partial_done` stays `False`, remaining is always 1.0, which is correct.

---

## Broker-Level Guard (belt-and-suspenders)

The `BrokerRiskGateway` in `src/broker/broker_risk_gateway.py` independently blocks
fractional-quantity orders via `_check_partial_tp_qty`. The fix in `tick_risk_manager.py`
is an upstream guard that prevents the partial event from ever being generated, so the
gateway check is never reached for single-contract positions.

Both guards now protect the system at different layers:

| Layer | Guard |
|-------|-------|
| Risk manager (order generation) | `trade.contracts >= 2` before partial exit path |
| Broker risk gateway (order submission) | `_check_partial_tp_qty` rejects qty < 1 |

---

## Updated Backtest Assumptions

All L2 backtests already assumed **full exit at target** (no partials). This is documented
in `tick_l2_backtest.py` via the `_scan_exit()` function which never implements partial
closes. The backtest P&L figures are therefore unaffected.

For the OHLCV-bar backtests in `src/backtesting/`, the partial exit was never implemented
in the vectorized exit path — only in the live-execution risk manager. Backtest results
remain valid as-is.

---

## Deployment Guidance

**For single-contract trading (default — all current live strategies):**
- Keep `use_ratchet=True` (default). The ratchet trailing stop is the correct single-contract
  analogue: it locks in profit at +0.5R and +1.5R without closing early.
- If you ever want to test `use_ratchet=False`, you must also trade ≥ 2 contracts.

**For 2+ contract expansion (future):**
- Partial TP at `partial_exit_r` (default 1.5R) remains fully functional.
- The `trail_to_breakeven=True` default moves the stop to entry cost after the partial fill.
- Size the full position so that the partial (50%) still meets Topstep's one-contract minimum.

---

## Files Changed

| File | Change |
|------|--------|
| `04_codebase/tick_risk_manager.py` | Added `and trade.contracts >= 2` guard in section 2b |

No other files were modified. No backtest re-runs required.
