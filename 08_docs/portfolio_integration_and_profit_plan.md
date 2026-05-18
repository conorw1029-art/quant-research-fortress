# Portfolio Integration and Profit Maximisation Plan
**Date:** 2026-05-18  
**Status:** Active — V678 stress test running, executor wired for V6/V7/V8

---

## 1. Where We Are

### Existing portfolio (15 strategies, IDs 1–15)
All current strategies use L2/microstructure signals (CVD, order book imbalance, delta).

| ID | Key | Status | Signal type |
|---|---|---|---|
| 2 | ES/cvd_divergence_large_print/15m | DEMO_CANDIDATE | CVD + volume |
| 3 | ES/cvd_divergence/15m | ENABLED_DRY_RUN | CVD |
| 4 | ES/tape_absorption/15m | ENABLED_DRY_RUN | Volume absorption |
| 7 | ES/prev_session_sweep/3m | ENABLED_DRY_RUN | Session level |
| 9 | GC/session_momentum_follow/3m | ENABLED_DRY_RUN | Session momentum |
| 13 | ES/key_level_cvd_rejection/15m | REVIEW_REQUIRED | Key level + CVD |
| 14 | NQ/key_level_cvd_rejection/15m | REVIEW_REQUIRED | Key level + CVD |

**Critical gap:** Every current ENABLED strategy uses CVD or delta — signals that are highly correlated. If the order flow data is noisy or missing, the entire portfolio fails together.

### New V678 strategies (IDs 16+, pending stress test)
Pure OHLCV. No CVD. No L2 required. Different data source → near-zero correlation to existing.

Signal types tested:
- VWAP mean reversion (V6)
- Prior day H/L breakout/sweep (V6)
- Opening range breakout/fakeout (V6)
- Donchian channel breakout (V7) ← strongest so far
- EMA crossover (V7)
- Keltner/Bollinger breakout (V7)
- RSI-2 / RSI momentum (V7)
- Consecutive close momentum (V8)
- Inside bar breakout (V8)
- Pivot reversal (V8)

---

## 2. Why This Combination Produces Maximum Profit

### The portfolio Sharpe formula
```
Portfolio_Sharpe = avg_Sharpe × √n_strategies / √(1 + (n-1) × avg_correlation)
```

If existing 5 strategies have avg Sharpe = 1.3 and average mutual correlation = 0.6:
- Current: 1.3 × √5 / √(1 + 4×0.6) = **1.64**

Add 5 uncorrelated V678 survivors (avg Sharpe = 1.5, correlation to existing ≈ 0.05):
- Combined 10 strategies, mixed correlation ~0.3: 1.4 × √10 / √(1 + 9×0.3) = **2.80**

That's a **71% improvement in risk-adjusted returns** just from diversification — not from finding better strategies.

### Why V678 and CVD are near-zero correlated
- CVD strategies fire when order flow imbalance reaches threshold → microstructure signal
- Donchian/EMA/Momentum strategies fire when price breaks N-bar high/low → pure price action
- These can fire simultaneously (confirming) or independently (diversifying)
- On days when order flow is noisy, price-action signals still work
- On sideways days where price-action is random, CVD may still catch institutional flow

### Instrument diversification
Current: ES and GC dominate.
V678 adds: SI (silver) as a new instrument with proven survivors.  
SI is correlated to GC (~0.6) but NOT to ES — adding 3 SI strategies contributes genuinely new return streams.

---

## 3. The Exact Path to Profit

### Stage 1: Expand the dry-run portfolio (NOW — week of May 19)
After stress test passes:

1. Add V678 survivors to `live_strategy_allowlist.yaml` as `ENABLED_DRY_RUN` (IDs 16–25)
2. Add corresponding PORTFOLIO entries to `tick_live_executor.py`
3. Run: `tick_live_executor.py --poll 60` (dry-run, all strategies)
4. Let 2+ RTH sessions accumulate signal logs

**Expected outcome:** 10–20 new signals per day across V678 strategies.  
No money at risk. Zero broker connection needed.

### Stage 2: Live data validation (Week 2, May 26+)
Once bar builder is running with Tradovate credentials:

For each V678 survivor:
- Does signal hour distribution match backtest? (expect similar 0900–1400 CT clustering)
- Is signal frequency consistent with expected rate?
- Any duplicate signals per bar?

**Pass criteria:** Signal log matches backtest distribution within ±30%.

### Stage 3: Demo promotion decision (Week 3, June 2+)
Criteria to promote a V678 strategy to DEMO_CANDIDATE:
- 2+ live dry-run sessions with valid signals
- Signal timing matches backtest
- No code errors in 48h dry-run window
- Worst-day micro risk ≤ $200 (enforced by tick_tradovate_client.py)

**Strategy 2 runs demo in parallel.** V678 strategy starts its own parallel demo session.

### Stage 4: Funded account expansion (Week 4+, June 9+)
After Week 4 degradation report on Strategy 2:
- If Strategy 2 degrades < 30%: add first V678 strategy to same account
- If degradation > 30%: run V678-only demo first, investigate Strategy 2
- Never add more than 3 strategies to one Topstep account simultaneously

### Stage 5: The funded account scaling ladder
Each Topstep $50K account runs max 2-3 strategies.  
With 10 funded accounts (~$1,000 runway each, ~$500 target/month per account):

```
Target: $5,000/month total from 10 accounts
= $500/account/month
= ~25 winning trades × $20 avg R × 1 micro contract

On 1 micro contract per trade:
- MES: $20/point × 0.1 = $2/point → ~$40/trade win at 2R
- MGC: $10/point × 0.1 = $1/point → ~$15/trade win at 2R
- MSI: $50/oz × 0.1 = $5/point → ~$75/trade win at 2R
```

The leverage increase only happens AFTER:
a) Account equity increases (funded account balance recovered)
b) 3+ months of live demo confirming strategy degradation < 30%
c) Separate evaluation sprint signed off

---

## 4. How New Strategies Connect to the Existing System

### In tick_live_executor.py (ALREADY DONE)
```python
# V6/V7/V8 are now imported with try/except guards
# compute_signal() now dispatches version="v6"/"v7"/"v8"
```

### Adding a new strategy — exact template

**Step 1:** Add to PORTFOLIO in tick_live_executor.py:
```python
(16, "GC", 15, "donchian_breakout",
 {"n": 20, "confirm": 1},
 None, None, "v7"),    # 1t-Sharpe=X, Regimes=Y/Z yrs, TS=Z%, Worst-micro=$W
```

**Step 2:** Add to live_strategy_allowlist.yaml:
```yaml
16:
  key: "GC/donchian_breakout/15m"
  status: ENABLED_DRY_RUN
  reason: >
    V7 strategy. WFO DSR=2.54, 1t-Sharpe=X.XX after stress test.
    Topstep Z% compliance. Worst day $W micro. All hours.
    Needs 2+ live dry-run sessions before demo promotion.
  worst_day_usd: -XXXX
  trade_count: XXXX
  sharpe_1t: X.XX
  topstep_compliant: true
  added_date: "2026-05-18"
```

**Step 3:** Run startup checklist to confirm new strategy recognized:
```bash
python -X utf8 tick_startup_checklist.py --quick
```

**Step 4:** Run dry-run executor — new strategy will appear in signal log:
```bash
python -X utf8 tick_live_executor.py --poll 60
```

### Signal quality check after 2 sessions
```bash
python -X utf8 tick_signal_log_reader.py --days 2 --strategy 16
```
Verify: signals fire, no errors, hour distribution reasonable.

---

## 5. Correlation Management

To maximise Sharpe without maximising drawdown, the live portfolio should:

### Rule 1: Max 2 strategies per instrument per session
Running 5 ES strategies simultaneously means a bad ES session wipes all 5.  
Cap: 2 ES + 2 GC + 1 SI = 5 strategies maximum per session.

### Rule 2: Never run two strategies with the same core signal
Bad: ES/cvd_divergence + ES/cvd_divergence_large_print (same CVD, just different threshold)  
Good: ES/cvd_divergence + GC/donchian_breakout (different instrument, different signal)

### Rule 3: Priority signals for capital allocation

**Tier 1 (highest confidence — full micro sizing):**
- WFO DSR ≥ 2.0 AND 1t-Sharpe ≥ 1.5 AND regime pass 6+/7 years

**Tier 2 (dry-run eligible — full sizing after 2 sessions):**
- WFO DSR ≥ 1.5 AND 1t-Sharpe ≥ 1.0 AND regime pass 5+/7 years

**Tier 3 (observation only — 0.5 micro or no size):**
- WFO DSR ≥ 1.0 AND 1t-Sharpe ≥ 1.0 but fewer than 5 regime years

### Rule 4: Daily portfolio halt
If combined daily P&L reaches -$300 on micros across all strategies:
- No new entries for remainder of session
- StateManager `record_strategy_halt()` called for all active strategies
- Resume next session automatically

---

## 6. What "Rigorously Tested" Means at Each Gate

### Gate A: WFO + DSR (DONE for V678)
- Walk-forward with 2000-bar train / 500-bar test windows
- DSR penalty for parameter count (prevents overfitting)
- Out-of-sample only — no in-sample contamination

### Gate B: Stress test (RUNNING NOW)
- Slippage sweep: 0t / 0.5t / 1.0t / 2.0t — must survive 1t
- Annual regime: 70% of calendar years positive (GC/SI only, have 6 years)
- Topstep daily compliance: 95% of trading days within $4,500 limit
- Worst-day micro risk: flag if >$200, exclude if >$500

### Gate C: Monte Carlo (prior survivors only — needs portfolio backtest)
- Shuffle trade order 10,000 times
- P(trailing drawdown > $7,500) < 5%
- Currently: existing 5 survivors at P=0.15% — PASS

### Gate D: Live dry-run validation (Week 2)
- Signals fire on real bars from Tradovate feed
- Hour distribution within ±30% of backtest
- No duplicate signals, no crashes, 48h stable

### Gate E: Demo with real bracket orders (Week 3+)
- At least 10 completed bracket orders (entry + exit)
- Fill quality within 2 ticks of intended price
- No CRITICAL reconciliation events
- Win rate within 20 percentage points of backtest expectation

---

## 7. Expected Profit Outcomes

### Conservative (current 5 survivors only, demo starts)
- 5 strategies × ~1 signal/week = ~5 trades/week
- At 40% win rate, 3R target/1R stop: R-multiple = 0.4×3 - 0.6×1 = +0.6R per trade
- $20 avg R on MES × 0.6R × 5 trades/week = **$60/week = $3,120/year per account**
- On 10 accounts: **$31,200/year**

### Target (current + 5 V678 survivors, all validated)
- 10 strategies × ~1.5 signals/week = ~15 trades/week
- At improved 45% win rate from better-filtered V678 signals: 0.45×3 - 0.55×1 = +0.80R
- $30 avg R (mix of GC + ES + SI micros) × 0.80R × 15 trades/week = **$360/week**
- On 10 accounts: **$187,200/year**

### What's needed to get there
1. Complete V678 stress test → identify 5 qualified survivors
2. Add to executor dry-run this week
3. Get Tradovate credentials → bar builder → live validation Week 2
4. Promote Strategy 2 + best V678 to demo Week 3
5. Degradation report Week 4 → decide expansion
6. Each funded account gradually moves from demo mode to live as gates pass

---

## 8. The One Number That Matters

**Win percent is less important than R-multiple.**

With 1.5R target / 1R stop (current setting):
- Break-even win rate = 1/(1+1.5) = 40%
- Our backtested V678 win rates: 35–45%
- We're profitable at 40%+ win rate with 1.5R target

**To improve win percent:**
- Tighter signal filters (higher DSR threshold → fewer but cleaner signals)
- Time-of-day filter (eliminate signals during known noisy hours)
- Regime filter (only trade when 20-bar MA slope positive for longs)

These filters are already embedded in the V7 strategies (EMA_CROSSOVER has slope_bars, RSI2_REVERSAL has trend_ema filter, MA_SLOPE_REGIME filters by MA direction).

**To improve R-multiple:**
- Wider TP (3R instead of 1.5R) → reduces win% but improves expectancy if signal quality holds
- The backtest engine currently uses STOP_MULT=1.5, TP_MULT=3.0 → already at 2:1 R

---

*Update this document after stress test results and after each live gate passes.*
