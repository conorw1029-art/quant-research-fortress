# Current System Truth Audit
**Generated:** 2026-05-15  
**Source:** Live file reads — not memory. All figures from actual jsonl/py files.

---

## 1. Zoo Database PASS Survivors

File: `05_backtests/zoo.jsonl`  
Total records: **1,096** (1,078 FAIL, 18 PASS)

The 18 PASS records represent **7 unique strategies** (re-runs create duplicates).

| Strategy | Instrument | DSR | PF | n Trades | Cost Model | Note |
|----------|-----------|----:|---:|---------|-----------|------|
| `fomc_drift` | MES | 1.627 | 2.893 | 57 | realistic (0.748 pts/RT) | 4 records; duplicate re-runs |
| `fomc_drift_zn` | ZN | 1.107 | 2.055 | 57 | realistic (0.036 pts/RT) | 5 records; all identical — 5 re-runs |
| `donchian_breakout_cl` | MCL | 4.476 | 2.998 | 236 | realistic (0.032 pts/RT) | 3 records; duplicate re-runs |
| `bollinger_rsi_gc` | MGC | 4.590 | 1.497 | 2,314 | realistic (0.324 pts/RT) | ✓ confirmed |
| `bollinger_rsi_fxe` | M6E | 11.254 | 1.465 | 9,453 | realistic (**0.000 pts/RT**) | ⚠️ ZERO COST — see issues below |
| `vwap_reclaim_gc` | MGC | 12.269 | 3.249 | 1,408 | realistic (0.324 pts/RT) | ✓ confirmed |
| `vwap_reclaim_si` | SIL | 5.117 | 4.192 | 241 | realistic (0.012 pts/RT) | ✓ confirmed |

### Issues in Zoo

- **Duplicate records**: `fomc_drift_zn` appears 5 times with identical DSR/PF/n — same run repeated across sessions. `donchian_breakout_cl` appears 3 times. Not corrupted, but inflates record count.
- **`bollinger_rsi_fxe` at zero cost**: `TransactionCost(M6E, realistic, 0.000pts/RT)` means M6E transaction costs were coded as 0 when this was evaluated. The DSR=11.254 is therefore unreliable. At realistic M6E costs this may not pass. **Not confirmed as a survivor in the current batch campaign.**
- **`fomc_drift_zn` barely passes**: DSR=1.107 (threshold is 1.0) with only 57 OOS trades. Marginally valid. Not included in the current 8-survivor count pending Step 2 confirmation.

---

## 2. Batch Campaign Survivors (5–9)

These are in batch result files — **not yet written to zoo.jsonl**.

### Batch 5 — Trend Following (Step 1, file: `batch5_results.jsonl`)

| Key | Instrument | DSR | PF | n |
|-----|-----------|----:|---:|--|
| `ma_trend_entry_gc` | MGC | +1.472 | 1.880 | 268 |
| `keltner_breakout_gc` | MGC | +1.701 | 1.429 | 1,107 |
| `vol_adj_momentum_gc` | MGC | +6.392 | 2.070 | 1,016 |
| `donchian_intraday_gc` | MGC | +6.593 | 1.914 | 1,327 |

### Batch 5 — Step 2 Stress Results (`batch5_step2.jsonl`)

All 4 have Step 2 records. `vol_adj_momentum_gc` and `donchian_intraday_gc` = **ALL-CLEAR**. `ma_trend_entry_gc` and `keltner_breakout_gc` = **CONDITIONAL** (fail 2x_cost and/or Topstep).

### Batch 6 — RTH ORB (`batch6_results.jsonl` + `batch6_step2.jsonl`)

| Key | Instrument | DSR | PF | n | Step 2 |
|-----|-----------|----:|---:|--|--------|
| `rth_orb_gc` | MGC | +5.391 | 1.924 | 1,023 | **ALL-CLEAR** |

### Batches 7–8 — 0/32 PASS
No survivors. All intraday strategies fail on non-Gold markets. All daily trend strategies fail on bonds, FX, and crypto.

### Batch 9 — Calendar Events (still running at audit time)

7 of 12 results in as of audit. All failing:

| Key | DSR | PF | n |
|-----|----:|---:|--|
| `nfp_drift_es` | -0.921 | 1.184 | 68 |
| `nfp_drift_zn` | -2.388 | 0.729 | 72 |
| `nfp_drift_gc` | -2.411 | 0.716 | 65 |
| `nfp_drift_cl` | -1.502 | 0.978 | 70 |
| `cpi_drift_es` | -0.574 | 1.368 | 71 | ← near-miss on PF |
| `cpi_drift_zn` | -3.025 | 0.592 | 71 |
| `cpi_drift_gc` | -1.594 | 0.942 | 65 |

Remaining: `eia_inventory_cl`, `eia_inventory_ng`, `ecb_drift_fxe`, `fed_minutes_es`, `fed_minutes_zn`.

---

## 3. Confirmed ALL-CLEAR Survivor Count

**8 fully confirmed ALL-CLEAR survivors** (Step 1 + Step 2 complete):

| # | Key | Strategy | Instrument | DSR | PF | n |
|---|-----|----------|-----------|----:|---:|--|
| 1 | `fomc_drift` | FOMC drift | MES | 1.627 | 2.893 | 57 |
| 2 | `donchian_breakout_cl` | Daily Donchian | MCL | 4.476 | 2.998 | 236 |
| 3 | `bollinger_rsi_gc` | Bollinger+RSI | MGC | 4.590 | 1.497 | 2,314 |
| 4 | `vwap_reclaim_gc` | VWAP reclaim | MGC | 12.269 | 3.249 | 1,408 |
| 5 | `vwap_reclaim_si` | VWAP reclaim | SIL | 5.117 | 4.192 | 241 |
| 6 | `vol_adj_momentum_gc` | Z-score momentum | MGC | 6.392 | 2.070 | 1,016 |
| 7 | `donchian_intraday_gc` | Intraday Donchian | MGC | 6.593 | 1.914 | 1,327 |
| 8 | `rth_orb_gc` | RTH ORB | MGC | 5.391 | 1.924 | 1,023 |

**2 CONDITIONAL survivors** (fail 2x_cost or Topstep):
- `ma_trend_entry_gc` — fails 2x_cost only
- `keltner_breakout_gc` — fails 2x_cost, miss_20%, Topstep

**Pending investigation:**
- `fomc_drift_zn` (zoo PASS, DSR=1.107, no Step 2 stress run)
- `bollinger_rsi_fxe` (zoo PASS but tested at zero cost — unreliable)

---

## 4. Step 1 / Step 2 Files

| File | Exists | Records | PASS |
|------|--------|---------|------|
| `zoo.jsonl` | ✓ | 1,096 | 18 |
| `batch5_results.jsonl` | ✓ | 12 | 4 |
| `batch5_step2.jsonl` | ✓ | 4 | 4 |
| `batch6_results.jsonl` | ✓ | 8 | 1 |
| `batch6_step2.jsonl` | ✓ | 1 | 1 |
| `batch7_results.jsonl` | ✓ | 19 | 0 |
| `batch8_results.jsonl` | ✓ | 13 | 0 |
| `batch9_results.jsonl` | ✓ | 7 (in progress) | 0 |

---

## 5. RiskManager Module

Location: `04_codebase/src/risk/`

| File | Size | Purpose |
|------|------|---------|
| `__init__.py` | 1,297 B | Exports RiskManager, AccountState, etc. |
| `risk_config.py` | 3,435 B | Config dataclass: limits, thresholds, account size |
| `risk_events.py` | 3,242 B | Event types: trade signals, risk breaches |
| `account_state.py` | 5,001 B | Tracks equity, daily PnL, drawdown state |
| `position_sizer.py` | 4,931 B | ATR-based position sizing |
| `risk_manager.py` | 15,375 B | Main class: enforces daily loss, trail DD, Topstep rules |

All files exist. Module is fully implemented.

---

## 6. Portfolio Backtest

File: `04_codebase/portfolio_backtest.py`  
Uses: **5 original survivors only** (fomc_drift, donchian_breakout_cl, bollinger_rsi_gc, vwap_reclaim_gc, vwap_reclaim_si)  
Does NOT include Batch 5–6 survivors (vol_adj_momentum_gc, donchian_intraday_gc, rth_orb_gc).

---

## 7. Current Portfolio Result

File: `05_backtests/portfolio_results.jsonl`  
Run date: 2026-05-14  
Survivor set used: 5 strategies

| Metric | Value |
|--------|-------|
| Portfolio DSR | **8.84** |
| Portfolio PF | 2.029 |
| Max Drawdown | **$7,101** |
| Topstep Result | **PASS** |
| Daily violations | 0 |
| Terminal | False |
| Final equity | $316,359 |
| Overall verdict | PROCEED_TO_LIVE |

**Annual P&L (2014–2025):**

| Year | P&L ($) | Positive? |
|------|---------|-----------|
| 2014 | +$48,602 | ✓ |
| 2015 | -$3,654 | ✗ |
| 2016 | -$1,236 | ✗ |
| 2017 | +$2,148 | ✓ |
| 2018 | +$11,543 | ✓ |
| 2019 | +$3,119 | ✓ |
| 2020 | +$47,472 | ✓ |
| 2021 | +$8,874 | ✓ |
| 2022 | +$24,394 | ✓ |
| 2023 | +$534 | ✓ |
| 2024 | +$9,407 | ✓ |
| 2025 | +$15,156 | ✓ |

**10/12 positive years.**

Note: This portfolio result is stale — it uses only 5 of the 8 confirmed survivors. An updated portfolio backtest with all 8 should be run after batches complete.

---

## 8. Stale / Duplicate Zoo Records

- `fomc_drift_zn`: 5 identical entries (same DSR/PF/n, different timestamps). Safe to ignore duplicates — use most recent.
- `donchian_breakout_cl`: 3 identical entries. Same situation.
- `fomc_drift`: 4 entries across conservative/optimistic/realistic cost scenarios. Realistic is the authoritative one.
- `bollinger_rsi_fxe`: 1 entry at 0 cost. **Flag for re-evaluation at realistic costs before trusting.**

No deleted records, no corrupted JSON detected.

---

## 9. Build Status Summary

### Definitely Built ✓
- Strategy registry (`src/zoo/registry.py`) — 12+ batches of strategies registered
- WFO engine (`run_strategy.py`, `src/backtesting/`)
- Performance metrics + DSR (`src/backtesting/metrics.py`)
- Go/No-Go evaluator
- Cost model (`run_strategy.py:build_cost_model`)
- All 8+ survivor strategy files
- Zoo database (`zoo.jsonl`, `zoo_reevaluate.py`)
- Batch stress test scripts (batches 5–9)
- Step 2 stress suite (batch5_step2.py, batch6_step2.py pattern)
- RiskManager module (`src/risk/`)
- Portfolio backtest script (`portfolio_backtest.py`)
- Portfolio results (5-survivor run complete)
- Remote ops scripts (`scripts/`)

### Partially Built ⚠️
- **Portfolio backtest**: Built and run, but uses only 5/8 survivors. Needs update.
- **`fomc_drift_zn`**: Zoo PASS, no Step 2 stress run.
- **`bollinger_rsi_fxe`**: Zoo PASS, but at zero cost — needs re-evaluation.
- **Batch 9**: Calendar events still running (7/12 complete, 0 PASS so far).
- **Zoo integration for Batch 5–8**: New batch results exist in jsonl files but not written to zoo.jsonl.

### Not Yet Built ✗
- **Batch 10+**: FX expansion (6C, 6A), Crypto dedicated batch, L2/MBP data pipeline
- **Updated portfolio backtest**: With all 8 confirmed survivors
- **Signal delivery / VPS pipeline**: Post-portfolio step
- **ML meta-labeling**: Phase 6
- **Paper trading infrastructure**: Planned for after all batches

---

## Recommendation

Safe to continue batch campaign (Batch 9 completing now). Once Batch 9 finishes:
1. Run updated portfolio backtest with all 8 confirmed ALL-CLEAR survivors.
2. Investigate `fomc_drift_zn` (borderline DSR=1.107) — run Step 2 if worth including.
3. Re-evaluate `bollinger_rsi_fxe` at realistic M6E costs before counting it as a survivor.
4. Remaining batches are lower priority given the diminishing returns trend.
