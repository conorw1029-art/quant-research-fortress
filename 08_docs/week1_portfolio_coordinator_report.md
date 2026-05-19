# Week 1 Portfolio Coordinator Report

**Date**: 2026-05-19  
**Status**: DRY-RUN READY — NOT DEMO AUTO-TRADE READY — NOT LIVE READY

---

## Summary

This report closes out the portfolio coordinator implementation sprint. It answers the 12 gate questions from `things_next.txt`.

---

## Files Created / Modified

| File | Action |
|------|--------|
| `08_docs/portfolio_netting_execution_model.md` | Created — documents virtual positions, broker net positions, and coordinator logic with 3 worked examples |
| `04_codebase/tick_portfolio_coordinator.py` | Created — pure Python coordinator implementation, no external dependencies |
| `04_codebase/test_portfolio_coordinator.py` | Created — 15-test suite, hand-rolled runner, no pytest required |
| `08_docs/strategy_deployment_eligibility.md` | Updated — R8, R9, R10 added |
| `08_docs/trade_count_update_audit.md` | Created — trade count source audit for all 38 strategies |
| `04_codebase/tick_live_executor.py` | Updated — coordinator import, coordinator gate in check_all_strategies, banner lines |
| `04_codebase/live_strategy_allowlist.yaml` | Updated — IDs 16–38 added (from V678 stress test) |

---

## Gate Questions

### 1. Was the separate strategy-position assumption corrected?

**YES.**

The previous model treated strategy-level positions as independent broker-level positions. The new model explicitly distinguishes:

- `VirtualStrategyPosition` — internal attribution tracking, never sent to broker
- `BrokerNetPosition` — actual account exposure (one net qty per symbol per account)
- `PortfolioCoordinator` — mandatory gate that prevents any SignalIntent from reaching `place_bracket_order()` without explicit approval

The old unsafe assumption ("Strategy 16 can be long GC and Strategy 17 can be short GC simultaneously in the same account") is now blocked by Rule 3 (REVERSE_POSITION_BLOCKED) and by Rule 9/10 of `detect_same_symbol_conflicts()`.

---

### 2. Does the system now distinguish virtual strategy positions from broker net positions?

**YES.**

The coordinator data model has two distinct classes. `VirtualStrategyPosition` carries strategy attribution (P&L, entry, stop, target per strategy). `BrokerNetPosition` carries the actual broker exposure and bracket order IDs. No strategy signal can assume it is the only holder of a broker position.

---

### 3. Are same-symbol opposite signals blocked?

**YES.**

Two mechanisms enforce this:

1. **Batch evaluation** (`evaluate_signals`): `detect_same_symbol_conflicts()` identifies all strategy IDs sending opposing directions on the same symbol in the same poll cycle. All are tagged `REJECT_CONFLICT` before any reaches the broker.

2. **Single evaluation** (`evaluate_single_signal`): Rule 3 blocks any signal that would reverse an existing broker position (unless `allow_reversal=True` in config).

Tested in T03, T04, T06.

---

### 4. Is max_net_contracts_per_symbol enforced?

**YES.**

Rule 7 enforces `max_net_contracts_per_symbol` (default: 1). If `allow_position_increase_same_symbol=False` (default), any signal that would add to an already-open same-direction position is rejected with `REJECT_SYMBOL_LIMIT`.

Config also supports `max_net_contracts_per_symbol > 1` for future use.

Tested in T05, T11.

---

### 5. Is first demo restricted to one strategy only?

**YES (in DEMO mode).**

`CoordinatorConfig.one_strategy_only_demo=True` rejects every `SignalIntent` whose `strategy_key` does not match `demo_strategy_key`. The executor sets `one_strategy_only_demo=True` and `max_total_open_symbols=1` when `mode == MODE_DEMO`.

In DRY_RUN mode, both settings are permissive (False / 10) to allow full multi-strategy dry-run testing.

Tested in T02, T12.

---

### 6. Are unverified trade counts blocked from deployment eligibility?

**PARTIALLY.**

The trade count audit (`trade_count_update_audit.md`) documents which counts are from WFO OOS `n_trades` (VERIFIED, IDs 16–38) and which are signal transition counts (UNVERIFIED, V1–V5). The audit recommends adding `trade_count_source`, `trade_count_verified`, `trade_count_method`, and `last_verified_at` fields to the allowlist.

However, the allowlist YAML has not yet been updated with these fields. Existing deployment blocks for V1–V5 strategies are based on other criteria (worst-day risk, short history, pending regime checks) — so no strategy that would otherwise be promoted is currently held back only by an unverified trade count. The formal YAML field update is a low-priority task that should be done before any V1–V5 strategy is considered for DEMO_CANDIDATE elevation.

---

### 7. Were any API calls made?

**NO.**

All coordinator code is pure Python stdlib. No network calls of any kind. No Tradovate connections. No data feed connections.

---

### 8. Were any orders placed?

**NO.**

The executor remains in `MODE_DRY_RUN` by default. The coordinator only produces `CoordinatorDecision` objects — it does not route to `place_bracket_order()`. No order submission path was opened.

---

### 9. Is it safe to continue to bracket mock implementation next?

**YES.**

The coordinator is implemented and tested. The executor routes new entry signals through the coordinator in dry-run. The broker position tracking uses the coordinator's `BrokerNetPosition` abstraction, which is compatible with the bracket order system (via `active_bracket_ids`). Bracket mock work can proceed.

The next bracket step should use `BrokerNetPosition.active_bracket_ids` as the single source of truth for whether a bracket is attached to an open position.

---

### 10. Is it safe to connect demo credentials?

**NO.**

Gate 9 requires:
- Live Tradovate data feed connected
- Demo credentials authenticated
- `place_bracket_order()` implemented and exchange-verified on DEMO account
- At least 30 validated dry-run sessions with zero `HUMAN_REVIEW_REQUIRED` coordinator events
- Full reconciliation of coordinator virtual positions against broker positions

None of these requirements are met.

---

### 11. Is it safe to start demo auto-trading?

**NO.**

In addition to the credential gates above, demo auto-trading requires coordinator validation:
- 15 coordinator tests must all pass (currently: pending test run)
- 30+ dry-run sessions with coordinator active and zero HUMAN_REVIEW_REQUIRED events
- Strategy 2 (ES/cvd_divergence_large_print/15m) must be the only DEMO_CANDIDATE

---

### 12. Is it safe for funded accounts?

**NO.**

Funded accounts require all gates 1–12 to pass, including live data feed, bracket verification, reconciliation, extended dry-run validation, and manual sign-off. The system is DRY-RUN READY only.

---

## Current Classification

| Gate | Status |
|------|--------|
| DRY-RUN READY | YES |
| DEMO AUTO-TRADE READY | NO |
| LIVE READY | NO |
| SAFE FOR MULTI-STRATEGY AUTO-EXECUTION | NO |

---

## Next Recommended Command

Run the coordinator tests to verify all 15 pass:

```
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 04_codebase\test_portfolio_coordinator.py
```

If all 15 pass, the next sprint is: implement bracket mock in a dry-run context, using `BrokerNetPosition.active_bracket_ids` as the bracket tracking mechanism, and wiring bracket placement through a `MockBrokerClient` that the coordinator can query without touching Tradovate.
