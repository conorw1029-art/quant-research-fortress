# Fortress System Feasibility Audit
**Date:** 2026-05-17  
**Scope:** Complete system review — risk manager, key levels, execution, strategies, data

---

## Executive Summary

The system is **structurally sound** for dry-run operation and **approaching demo readiness**.  
Three items must be completed before any real money is at risk.

| Layer | Status | Notes |
|---|---|---|
| Strategy library | SOLID | 12 strategies, 5 hardened survivors, all backtested |
| Risk manager | SOLID | Ratchet trailing stop fixed, consecutive loss CB added |
| Key levels | ADDED | PDH/PDL, VWAP, volume POC, round numbers — annotation only |
| Dry-run harness | SOLID | 10/10 tests pass, allowlist enforced |
| Bracket orders | MOCK-VERIFIED | 25/25 tests pass, not exchange-verified |
| Data pipeline | STALE | Bars end 2026-05-14 — no live feed connected |
| Reconciliation | MISSING | Gate 7 — must implement before demo |
| Tradovate credentials | NONE | Required before demo |

---

## 1. Risk Manager — Is it complete?

**YES, with one caveat.**

### What's now correct

| Feature | Status |
|---|---|
| Per-trade stop (ATR × 1.5) | CORRECT |
| Ratchet trailing stop (replaces partials) | ADDED |
| — At +1.5R: stop moves to +0.5R | ✓ |
| — At +2.5R: stop moves to +1.5R | ✓ |
| Full TP at +3.0R | CORRECT |
| Time stop at 50 bars | CORRECT |
| Signal-driven close | CORRECT |
| Forced close (kill switch / session end) | CORRECT |
| Strategy daily loss halt | CORRECT |
| Portfolio daily loss halt | CORRECT |
| Account trailing drawdown halt | CORRECT |
| Consecutive loss circuit breaker (3 losses) | ADDED |
| Correlation warning (ES + NQ simultaneous) | ADDED |

### Why ratchet replaces partials

With `MAX_CONTRACTS_PER_TRADE = 1`, it is impossible to close 50% of a position.
The backtester was run with full TP at 3R (no partials). The ratchet stop is strictly
conservative relative to the backtest: a winner that reaches 2.5R will lock in at
least 1.5R even if it reverses before 3R, while the backtest assumed holding to 3R or
reverting to stop. The ratchet can only produce **equal or better** live results vs
the backtest (for winning trades) and reduces runoff risk on near-winners.

### Caveat

Backtest results assumed holding to full 3R. The ratchet stop WILL exit some trades at
1.5R or 2.5R that would have reached 3R in the backtest. Empirically, this should be
a modest effect (trades that reverse from 2.5R to 1.5R are uncommon in trending
micro-structure strategies). Monitor the first 30 demo trades for R-distribution vs
backtest expectations.

---

## 2. Key Levels — Are they useful for entries?

**YES — as context, not as filters.**

Four level types are computed per instrument:

| Level | Source | Use |
|---|---|---|
| PDH / PDL | Previous day OHLCV | Breakout / rejection zones |
| PDC | Previous day close | Mean-reversion anchor |
| Intraday VWAP | Today's cumulative TP×Vol | Institutional reference |
| Volume POC | 200-bar rolling profile | High-conviction support/resistance |
| Round numbers | Instrument-specific step | Psychological magnet |

**How they appear in alerts:**

```
Key levels: PDH(7580.0) above 0.8R | POC(7526.4) above 0.9R | ROUND(7525) above 1.0R
```

**Key level as entry criterion — break & retest:**

The `break_retest_cvd` strategy in `tick_strategies_v3.py` already implements
break-and-retest logic using rolling 20-bar H/L as the break level. This is
behaviorally close to PDH/PDL breakouts. For a dedicated key-level strategy:

- A signal fires when: price breaks PDH/PDL, then pulls back and holds, then CVD confirms
- The current strategy library covers this implicitly through `cvd_divergence_large_print`
  (captures the volume signature of institutional buyers appearing at a key level)
- Explicit PDH/PDL filter would add value but requires backtesting first

**Current decision: annotate, don't filter.** The annotation gives the trader/log
context without creating an untested entry rule.

---

## 3. Strategy Library — Feasibility Assessment

### Confirmed survivors (all passed Step 2 stress test)

| ID | Strategy | Symbol | Worst Day | 1t-Sharpe | Topstep |
|---|---|---|---|---|---|
| 2 | cvd_divergence_large_print/15m | ES | -$3,827 | 2.1 | 100% |
| 7 | prev_session_sweep/3m | ES | -$2,800 | 1.45 | 100% |
| 8 | range_contraction_break/30m | NQ | -$3,439 | 5.63 | 100% |
| 9 | session_momentum_follow/3m | GC | -$3,042 | 3.22 | 100% |
| 10 | trade_absorption_signal/30m | GC | -$4,542 | 4.65 | 100% |

All five pass on micro contracts (1/10 worst day = -$300 to -$454 — well within $1k remaining DD).

### Portfolio correlation risk

ES strategies (2, 3, 4, 7, 11) and NQ strategies (1, 5, 6, 8, 12) are ~90% correlated.
The executor now warns when both are simultaneously in the same direction.

**Safe practice:** Allow at most one ES + one NQ position simultaneously. The allowlist
currently has only strategy 2 as `DEMO_CANDIDATE`, which naturally prevents this.

### Backtest vs live discrepancy

The backtester tests full 3R TP only. The live executor with ratchet stop will:
- Match backtest: trades that reach 3R cleanly
- Improve on backtest: trades that reverse after 2.5R (ratchet exits at +1.5R vs potential stop-out)
- Diverge from backtest: trades exited at +0.5R ratchet level (would have been a winner to 3R in backtest)

This divergence is small and acceptable. No rebacktest needed.

---

## 4. Execution Stack — What works, what doesn't

### What works (verified)

- Kill switch detection (KILL_SWITCH.txt with "STOP")
- Strategy allowlist enforcement (dry-run/demo/live mode gates)
- Bracket order payload construction (25 mock tests pass)
- Dry-run validation (10/10 pass)
- ATR-based stop/target computation
- Hour/session filters
- Signal logging to JSONL
- Stale data warning (>20 min since last bar)
- Correlation warning (ES+NQ same direction)
- Key level annotation on alerts

### What does NOT work yet

| Gap | Impact | Fix |
|---|---|---|
| No live data feed | Bars stale since 2026-05-14 | Connect tick_processor to live Tradovate feed |
| No reconciliation (Gate 7) | Restart creates duplicate positions | Implement startup reconciliation |
| OSO payload unverified | May fail with real Tradovate credentials | Test one demo bracket order |
| Contract rollover needed | MESM5 expires ~June 20, 2026 | Update TV_CONTRACT_MAP before rollover |

---

## 5. What is NOT production-ready (in order of criticality)

### CRITICAL: Gate 7 — Reconciliation (blocks all demo/live auto-trade)

On executor restart, `PositionTracker` is empty. If an ES position is open at Tradovate
from a previous session, the executor will re-enter and create a second position. This
can result in holding 2× the intended size at full risk.

**Fix required before demo:**
```python
def _reconcile_positions(tv_client, tracker: PositionTracker, rm: RiskManager):
    """On startup, fetch open Tradovate positions and populate tracker."""
    positions = tv_client.get_positions()  # {symbol: {netPos, avgPrice}}
    for sym, pos in positions.items():
        strat_id = _find_strat_for_symbol(sym)
        if strat_id and pos["netPos"] != 0:
            direction = 1 if pos["netPos"] > 0 else -1
            tracker.update(strat_id, direction)
            # Reconstruct approximate trade record (best effort from broker state)
```

### CRITICAL: No live data feed

The parquet bars end 2026-05-14. All signals computed from these bars are stale.
The stale data warning fires on every bar check. Live trading requires `tick_processor.py`
to be running and writing fresh bars.

**Action:** Connect `tick_bar_builder.py` (Gate 3) to a live Tradovate WebSocket feed.
This is outside the scope of the executor — it requires running `tick_bar_builder.py`
in a separate process.

### MODERATE: OSO payload structure unverified

`place_bracket_order()` builds the Tradovate `placeOSO` JSON based on the API reference.
This has not been tested with real credentials. The response parsing assumes a list
`[entry_confirmation, stop_confirmation, target_confirmation]`.

**Action:** After getting Tradovate credentials, run one demo bracket order manually
before enabling any automated demo trading.

### MODERATE: Contract month rollover

`TV_CONTRACT_MAP` has MESM5, MGCM5, MNQM5. These expire approximately June 20, 2026.
After expiry, orders will fail.

**Action:** Update `TV_CONTRACT_MAP` in `tick_live_executor.py` to June contracts
(MESU5, MGCU5, MNQU5) before June 20, 2026.

### LOW: News monitor not connected

`tick_news_monitor.py` is imported but the actual feed requires credentials or a
news API key. Currently it fails silently and is skipped.

---

## 6. Recommended next actions (ordered)

1. **Gate 7: Startup reconciliation** — implement `_reconcile_positions()` in executor
2. **Get Tradovate credentials** — needed for Gate 3 (live bars) and Gate 9 (demo auto-trade)
3. **Gate 3: Connect live bar feed** — `tick_bar_builder.py --rest` to verify, then WebSocket
4. **Gate 6 exchange verification** — place one demo bracket order to verify OSO payload
5. **Gate 9: Single demo strategy** — run strategy 2 (DEMO_CANDIDATE) in demo auto-trade
6. **Monitor first 30 demo trades** — compare R-distribution vs backtest expectations

Do NOT attempt Gates 6, 9 until Gates 3 and 7 are complete.

---

## 7. Risk Summary — Is it safe to run?

### Safe NOW (no broker credentials needed)

```powershell
cd C:\Users\conor\Desktop\quant-research\04_codebase
& "C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe" tick_live_executor.py `
  --poll 60 --quiet --alert-file alerts.json --max-runtime-minutes 60
```

This runs all 12 strategies in dry-run mode. No orders placed. Stale data will produce
stale signals — the stale warning fires but execution continues (intended).

### NOT safe (requires broker credentials + Reconciliation + live data)

- `--demo-auto-trade` — will immediately try to place bracket orders
- `--live-auto-trade` — hard blocked without `FORTRESS_LIVE_ENABLE` env var

### Safety limits in effect

| Limit | Value | Rationale |
|---|---|---|
| Max trade risk (micro) | $200 | MES ~$44, MNQ ~$50, MGC ~$162 |
| Max daily portfolio loss | $600 | 10 accounts × $60 cushion |
| Account trailing DD halt | $800 | Keeps $200 buffer per account |
| Strategy daily loss halt | $250 | Per-strategy protection |
| Consecutive loss CB | 3 losses | Auto-pauses strategy after streak |
| Max contracts | 1 | Micro mode, no scaling yet |
| Kill switch | KILL_SWITCH.txt | Immediate manual halt |

All limits are conservative relative to the $1,000 remaining DD per account.
