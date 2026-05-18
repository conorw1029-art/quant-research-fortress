# 30-Day Fortress Development Roadmap
**Date:** 2026-05-18  
**Sprint period:** May 19 – June 17, 2026  
**Contract rollover deadline:** June 20, 2026 (MESM5/MNQM5/MGCM5 expire)

---

## Guiding Principles

1. Every week builds on the previous. Do not start Week 2 work before Week 1 is complete.
2. No live orders. No demo auto-trade before bracket orders and reconciliation are proven.
3. Every code change has a test. Every design decision has a document.
4. Progress is measured by gate status (BLOCKED → OPEN), not lines of code.
5. If a gate cannot be opened this sprint, record why and plan the data/credential acquisition separately.

---

## Pre-Sprint: Rollover Check (Do Before May 23)

The current contracts expire June 20, 2026 (~33 days from now). The rollover to U5 should happen during the last week of May or first week of June.

```bash
# Check current contract symbols in all three files:
python -X utf8 tick_contract_rollover.py --show

# When ready to roll (target: ~June 6-13 when liquidity shifts):
python -X utf8 tick_contract_rollover.py --to U5

# Verify the rollover:
python -X utf8 tick_contract_rollover.py --show
```

Rollover affects `TV_CONTRACT_MAP` in `tick_live_executor.py`, `tick_bar_builder.py`, and `tick_startup_checklist.py`.

---

## Week 1 — Execution Safety Design and Mocks (May 19–25) — **COMPLETE**

**Goal:** All bracket order mocked tests pass. State persistence designed. No broker connection.

**Results (2026-05-18):** 66 bracket mock tests PASS. 44 state manager tests PASS. 40 reconciliation tests PASS. Strategy 15 demoted. StateManager + tick_broker_reconciliation integrated into executor. Reconciliation log writing implemented. Exit gate PASSED.

### Day 1-2: Dry-Run Safety Checkpoint and Allowlist Review

```bash
# Verify current state:
python -X utf8 tick_dry_run_validation.py
python -X utf8 tick_startup_checklist.py --quick

# Review allowlist — confirm no strategy was accidentally promoted:
cat live_strategy_allowlist.yaml
```

- [ ] Confirm dry-run validation is 10/10
- [ ] Confirm only Strategy 2 is DEMO_CANDIDATE
- [ ] Review strategy_deployment_eligibility.md and update allowlist if any demotion is needed
- [ ] Confirm KILL_SWITCH.txt exists and reads "RUN"

### Day 2-3: Bracket Order Mock Test Suite

Write `tick_tradovate_client_mock_tests.py` or add test class to `tick_dry_run_validation.py`:

- [ ] Test 1: dry_run=True returns correct DRY_RUN mode result (never calls API)
- [ ] Test 2: All 19 safety validations trigger correctly with boundary inputs
- [ ] Test 3: Kill switch STOP blocks submission
- [ ] Test 4: Session closed blocks submission
- [ ] Test 4: BUY with stop above entry returns STOP_ABOVE_ENTRY_FOR_BUY
- [ ] Test 5: SELL with target above entry returns TARGET_ABOVE_ENTRY_FOR_SELL
- [ ] Test 6: Dollar risk > $200 returns ESTIMATED_RISK_EXCEEDS_LIMIT
- [ ] Test 7: Off-tick price returns OFF_TICK_PRICE
- [ ] Test 8: Dry-run result includes full payload for logging
- [ ] Test 9: Result includes all required fields: ok, mode, entry_order_id, stop_order_id, target_order_id, oco_id, oso_id, reason, payload

### Day 3-4: Add Missing Safety Validations to tick_tradovate_client.py

- [ ] Add kill switch check at start of `place_bracket_order()`
- [ ] Add session-open gate (check account_state.json or parameter)
- [ ] Add all 19 validation checks (stop-side, tick rounding, dollar risk)
- [ ] Add `oco_id` and `oso_id` fields to `BracketOrderResult`
- [ ] Add `client_order_id` parameter and tracking
- [ ] Re-run mock tests after each change

### Day 4-5: State Persistence Skeleton

Create `06_live_trading/state/` directory and stub implementations:

- [ ] Create directory structure
- [ ] Write `StateManager` class with atomic write helpers
- [ ] Implement `positions.json` read/write
- [ ] Implement `active_brackets.json` read/write
- [ ] Implement `daily_pnl.json` read/write
- [ ] Implement `strategy_halts.json` read/write
- [ ] Implement `heartbeat.json` write
- [ ] Add unit tests for atomic write, read-after-crash recovery
- [ ] Test that a simulated crash (truncated .tmp file) does not corrupt state

### Week 1 Exit Gate

**All must pass before Week 2 begins:**
- [ ] `tick_dry_run_validation.py` — 10/10 PASS including bracket mock tests
- [ ] Strategy allowlist reviewed, no unsafe promotions
- [ ] All 19 bracket safety validations implemented and tested
- [ ] State persistence skeleton exists with passing unit tests
- [ ] Kill switch check added to `place_bracket_order()`

---

## Week 2 — Live Data Dry-Run (May 26 – June 1)

**Goal:** Bar builder verified on live feed. Dry-run signals logged for at least one full RTH session per instrument.

**Prerequisite:** No broker connection. No orders. Data feed only.

### Day 6-7: Bar Builder Test (REST Polling)

```bash
# Start bar builder in REST mode:
python -X utf8 tick_bar_builder.py --rest --verbose

# Let it run for one full RTH session (6:30–13:00 CT / 11:30–18:00 UTC)
# Then check data freshness:
python -X utf8 tick_startup_checklist.py --quick
```

- [ ] REST polling bar builder runs without errors for one full session
- [ ] Parquet files are being written correctly
- [ ] CVD continuity is maintained if bar builder is restarted mid-session
- [ ] Stale data detection triggers correctly if feed is paused for > 5 minutes

### Day 7-8: Bar Builder Test (WebSocket)

```bash
# Start WebSocket bar builder:
python -X utf8 tick_bar_builder.py --verbose

# Monitor for disconnections; verify reconnect logic
```

- [ ] WebSocket bar builder runs without errors for one full session
- [ ] Reconnect logic recovers from a simulated disconnect (stop and restart)
- [ ] CVD continuity seeds correctly from last parquet row on restart
- [ ] Both MES and MGC bars are being produced at correct bar intervals

### Day 8-9: Contract Rollover Check

- [ ] Run `tick_contract_rollover.py --show` to confirm current contracts
- [ ] Check open interest / volume shift timing in CME calendar
- [ ] Plan rollover execution date (target: when volume crosses over, typically ~1 week before expiry)

### Day 9-10: Dry-Run One Strategy on Live Data

```bash
# Run executor in dry-run mode for Strategy 2 only, on live bars:
python -X utf8 tick_live_executor.py --strategy 2 --poll 60

# Check signal log after 1-2 sessions:
python -X utf8 tick_signal_log_reader.py --days 2 --strategy 2
```

- [ ] At least one signal fires during an RTH session
- [ ] Signal log entries have correct fields (event_type, strategy_id, symbol, bar_minutes)
- [ ] No duplicate signals for same bar
- [ ] Compare signal times with backtest expectations (similar hour distribution)
- [ ] No crashes or uncaught exceptions over a full session

### Day 10: Stale Data Detection Test

- [ ] Artificially pause the bar builder and verify `tick_startup_checklist.py` reports stale data
- [ ] Verify executor does not fire signals when bars are stale
- [ ] Verify `last_seen_bar.json` updates correctly

### Week 2 Exit Gate

**All must pass before Week 3 begins:**
- [ ] Bar builder (REST + WebSocket) runs clean for one full session per instrument
- [ ] CVD continuity verified after restart
- [ ] Strategy 2 dry-run signals logged for at least 2 RTH sessions
- [ ] Signal log content matches expected hour distribution
- [ ] No uncaught exceptions in any component
- [ ] Rollover plan confirmed

---

## Week 3 — Demo Only (June 2–8)

**Goal:** Strategy 2 running in demo mode with broker-native bracket orders, full reconciliation.

**BLOCKED UNTIL:** Tradovate demo credentials received + Week 1 and Week 2 exit gates passed.

### Before Demo Start: Credential Pre-Flight

```bash
# Set environment variables (never hardcode):
set TRADOVATE_USERNAME=your@email.com
set TRADOVATE_PASSWORD=yourpassword
set TRADOVATE_CID=12345
set TRADOVATE_SECRET=yoursecret

# Run full credential pre-flight:
python -X utf8 tick_credentials_test.py

# If all 5 gates pass, run bracket order test:
python -X utf8 tick_credentials_test.py --test-order
```

- [ ] All 5 credential gates pass
- [ ] Gate 6 (bracket order test) passes: far-below-market order placed and cancelled

### Day 11-12: Broker Reconciliation Implementation — **COMPLETE (done in Week 1)**

Broker reconciliation logic integrated into `tick_live_executor.py` on 2026-05-18:

- [x] Startup reconciliation: compare positions.json vs broker positions
- [x] Startup reconciliation: compare active_brackets.json vs broker open orders
- [x] All 10 reconciliation scenarios handled (see state_reconciliation_design.md)
- [x] Reconciliation result logged to broker_reconciliation_log.jsonl
- [x] Clean startup (both flat) → proceed to bar loop
- [x] Mismatch → halt new entries + alert console
- NOTE: Full live broker comparison requires credentials — current implementation compares local state vs empty snapshot (staleness check only). Real broker position fetch is wired via existing _reconcile_positions() for demo/live modes.

### Day 12-13: Demo Start — Strategy 2 Only

```bash
# Single strategy, demo mode, bracket orders required:
python -X utf8 tick_live_executor.py --strategy 2 --poll 60 --demo
```

Rules for Week 3 demo:
- [ ] Strategy 2 only (ES/cvd_divergence_large_print/15m)
- [ ] 1 micro contract (MES) only
- [ ] Broker-native bracket orders required on every entry
- [ ] Kill switch must work (test by setting KILL_SWITCH.txt to STOP mid-session)
- [ ] No GC strategies in demo this week
- [ ] No NQ strategies in demo this week

### Day 13-14: Monitor Demo Session Behaviour

```bash
# Check demo signal log after each session:
python -X utf8 tick_signal_log_reader.py --days 1 --trades
python -X utf8 tick_recent_performance.py --strategy 2
```

Monitor for:
- [ ] Fill quality: compare entry_px in log vs intended entry_px
- [ ] Slippage: actual fill vs limit price
- [ ] Bracket behaviour: do stop and target orders appear on broker platform?
- [ ] No duplicate orders
- [ ] No naked entries (position without bracket)
- [ ] Reconciliation running clean every 5 minutes

### Week 3 Exit Gate

**All must pass before Week 4 evaluation:**
- [ ] Demo ran for at least 3 full RTH sessions without crashes
- [ ] At least 1 completed bracket order (entry + stop or target filled)
- [ ] No CRITICAL reconciliation events in broker_reconciliation_log.jsonl
- [ ] Kill switch test passed (STOP kills demo within one bar loop)
- [ ] No unexplained positions on broker platform
- [ ] Fill quality within 2 ticks of intended entry price on average

---

## Week 4 — Evaluation (June 9–17)

**Goal:** Generate degradation report. Decide whether to continue demo, expand, or pause.

### Day 15-17: Live-vs-Backtest Degradation Report

Compare demo results against backtest expectations for Strategy 2:

```bash
python -X utf8 tick_signal_log_reader.py --days 7 --trades
python -X utf8 tick_recent_performance.py --strategy 2 --regime
```

Build `08_docs/live_vs_backtest_degradation_strat2.md`:
- [ ] Compare demo trade count vs expected rate (backtest had ~X trades/week)
- [ ] Compare demo win rate vs backtest win rate (backtest 42-50%)
- [ ] Compare demo avg R-multiple vs backtest avg R (backtest +0.2R avg)
- [ ] Estimate slippage cost per trade (fills vs intended prices)
- [ ] Compute execution-adjusted profit factor
- [ ] Flag any signals that fired but did not fill (missed entry)
- [ ] Flag any signals that fired outside expected hours

### Day 17-19: Execution Slippage Report

Build `08_docs/execution_slippage_report.md`:
- [ ] Entry slippage distribution
- [ ] Stop fill slippage distribution
- [ ] Target fill slippage distribution
- [ ] Worst-case fill event
- [ ] Impact on expected P&L if slippage continues at observed rate

### Day 19-21: Strategy Eligibility Review

Update `08_docs/strategy_deployment_eligibility.md` based on live data:
- [ ] Does Strategy 2 remain DEMO_CANDIDATE or should it be ENABLED_DRY_RUN?
- [ ] Is any other strategy eligible for demo based on Week 3 learning?

### Decision Gate (End of Week 4)

Based on Week 3 + Week 4 evidence, make one of five decisions:

| Decision | Trigger Condition |
|---|---|
| Continue demo + add Strategy 7 | Strategy 2 degradation < 30%, no CRITICAL events |
| Continue demo Strategy 2 only | Strategy 2 degradation 30–60%, needs more data |
| Pause demo, continue dry-run | Degradation > 60% or CRITICAL reconciliation events |
| Acquire more data | ES/NQ history needed, pause demo while data is sourced |
| Review system design | Fundamental execution or signal quality issue found |

**Do not expand to funded accounts regardless of Week 4 results without a separate evaluation sprint.**

---

## Rollover Timeline

| Date | Action |
|---|---|
| May 19–23 | Run `tick_contract_rollover.py --show` to confirm current symbols |
| May 26 – June 1 | Watch volume/OI for M5 vs U5 shift |
| June 6–10 | Execute rollover if volume has crossed to U5 |
| June 13 | Hard deadline — must be on U5 before June 20 expiry |
| June 20 | M5 contracts expire |

Rollover command:
```bash
python -X utf8 tick_contract_rollover.py --to U5
python -X utf8 tick_contract_rollover.py --show  # verify
python -X utf8 tick_startup_checklist.py --quick  # confirm new symbols resolve
```

---

## Gate Summary

| Gate | Opens When | Status |
|---|---|---|
| Week 1 Exit | Bracket mocks pass, state persistence skeleton built | **PASSED** (2026-05-18) |
| Week 2 Exit | Bar builder verified, 2+ sessions of dry-run signals | PENDING — blocked on credentials for bar builder |
| Week 3 Start | Tradovate credentials + Week 1 + Week 2 | **BLOCKED** (no credentials) |
| Week 3 Exit | 3 demo sessions, no CRITICAL events | PENDING |
| Week 4 Decision | Degradation report complete | PENDING |
| Funded accounts | Never in this sprint | BLOCKED — requires separate evaluation |

---

*Update this document each week as gates open or new blockers are identified.*
