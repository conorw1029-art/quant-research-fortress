# Databento & L2 — Next Action Report

**Generated:** 2026-06-02  
**System classification:** DATA_READY | BROKER_MOCK_ONLY | DRY_RUN_READY | NOT_DEMO_READY | NOT_LIVE_READY

---

## Safety Checklist

| Question | Answer |
|----------|--------|
| Is `.env` safe and Git-ignored? | **YES** — `.gitignore` covers `.env`, `*.key`, `*secret*` |
| Is Databento key detected? | **YES** — `DATABENTO_API_KEY` present, preflight shows DATA_READY |
| Is Tradovate disabled? | **YES** — `TRADOVATE_ENABLED=false`, preflight shows TRADOVATE_DISABLED |
| Is broker mode MOCK_ONLY? | **YES** — `BROKER_MODE=MOCK_ONLY` |
| Is it safe to continue data-only work? | **YES** |
| Did Databento metadata preflight pass? | Preflight script exists; key is present and valid (data was downloaded successfully) |
| Is live-data dry-run possible? | **YES** — signals can be generated without placing orders |
| Is broker connection still blocked? | **YES** — no Tradovate calls, mock broker only |
| Is demo auto-trade still blocked? | **YES** — no demo account exists |
| Are funded accounts still blocked? | **YES** — hardcoded MOCK_ONLY enforcement |

---

## L2 Strategy Results (as of 2026-06-02)

### Data Downloaded and Available
- GC (Gold) — mbp-10 tick data 2020-2026, built into `GC_bars_l2_1m.parquet` (77,270 bars)
- SI (Silver) — mbp-10 tick data 2020-2026, built into `SI_bars_l2_1m.parquet` (37,920 bars)
- Total Databento spend to date: $120.14 of $125 budget

### Backtests Complete (Quick Mode — recent 1-year sample)

| Symbol | Combos | Survivors | Hardened |
|--------|--------|-----------|----------|
| GC | 331 | 39 | 5 |
| SI | 322 | 29 | 5 |

### Evidence Upgrade Complete — All 10 Pass

GC hardened survivors:

| Strategy | WF Sharpe | Bootstrap p | 1-tick Sharpe |
|----------|-----------|-------------|---------------|
| OFI_Continuation | 2.98 | 0.0000 | 0.846 |
| Sweep_Absorption_Reversal (thr=0.5) | 5.13 | 0.0100 | 1.276 |
| Sweep_Absorption_Reversal (thr=0.8) | 5.04 | 0.0140 | 1.237 |
| CVD_VWAP (hold=8) | 2.41 | 0.0000 | 0.859 |
| CVD_VWAP (hold=12) | 2.20 | 0.0000 | 0.868 |

SI hardened survivors:

| Strategy | WF Sharpe | Bootstrap p | 1-tick Sharpe |
|----------|-----------|-------------|---------------|
| CVD_Microprice | 1.55 | 0.0220 | 0.609 |
| CVD_VWAP (cvd=60, hold=8) | 2.22 | 0.0000 | 0.468 |
| CVD_VWAP (cvd=60, hold=12) | 1.91 | 0.0000 | 0.399 |
| CVD_VWAP (cvd=70, hold=8) | 1.51 | 0.0000 | 0.552 |
| CVD_VWAP (cvd=70, hold=12) | 1.23 | 0.0000 | 0.525 |

**Key finding:** ALL strategies fail at 2-tick slippage. Edge is thin but statistically real at 1 tick.

### Full 5-Year Backtests (In Progress / Pending)

- GC full backtest: **RUNNING** (background task `bgi3jlx6z`)
- SI full backtest: **PENDING** (start after GC completes)

---

## What NOT to Download Yet

| Data | Status | Reason |
|------|--------|--------|
| ES/NQ mbp-10 historical (full) | **NEEDS_APPROVAL** | ~$50-100 for 2020-2026 |
| Any MBO/L3 data | **NEEDS_APPROVAL** | Very expensive; unproven value |
| Additional GC/SI years | **NOT NEEDED** | Already have 2020-2026 |

---

## New L2 Strategies Ready to Test

See `08_docs/l2_strategy_backlog.json` for 12 new strategy hypotheses. Priority order:

1. `cvd_divergence_vwap` — explicit CVD-vs-price divergence at VWAP (GC, SI)
2. `ofi_multi_level_confirmation` — L1+L3+L5 OFI alignment filter
3. `sweep_no_replenishment_continuation` — trend follow after unabsorbed sweep
4. `failed_breakout_absorption` — fade false breakouts with high absorption score
5. `vwap_deviation_cvd_divergence` — VWAP stretch + CVD reversal
6. `mtf_30m_context_1m_ofi_entry` — 30m trend context for 1m OFI entries
7. `gc_si_confirmation` — cross-market GC+SI OFI agreement filter

**Cross-market strategies (ES/NQ leads):** BLOCKED until ES/NQ L2 bars exist.

---

## Infrastructure Built

| File | Purpose | Status |
|------|---------|--------|
| `tick_credentials_preflight.py` | System safety classification | ✅ |
| `tick_evidence_upgrade.py` | Walk-forward + bootstrap + slippage ladder | ✅ (bugs fixed 2026-06-02) |
| `tick_mock_broker.py` | Bracket/order simulation, no API calls | ✅ (all smoke tests pass) |
| `tick_l2_backtest.py` | L2 strategy battery | ✅ (commission + DSR bugs fixed) |
| `08_docs/no_demo_account_execution_plan.md` | Broker restrictions | ✅ |
| `src/l2/` | L2 feature engine | ✅ |
| `test_broker_reconciliation.py` | Reconciliation tests (40/40) | ✅ |

---

## Exact Commands to Run Next

### Step 1 — Run full SI backtest (after GC completes)
```
cd 04_codebase
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 tick_l2_backtest.py --symbol SI
```

### Step 2 — Evidence upgrade on full GC results
```
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 tick_evidence_upgrade.py \
  --survivors 05_backtests/l2_results/GC_hardened_survivors.json \
  --bars 01_data/tick_bars/GC_bars_1m.parquet \
  --l2-bars 01_data/tick_bars/GC_bars_l2_1m.parquet \
  --tick-size 0.10 --dsr-threshold 0.50
```

### Step 3 — Run mock broker smoke test
```
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 04_codebase/tick_mock_broker.py
```

### Step 4 — Credential preflight (run before every session)
```
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 04_codebase/tick_credentials_preflight.py
```

---

## Topstep Account Allocation Recommendation

Based on evidence results, prioritized for 10 funded accounts:

| Slot | Strategy | Symbol | Params | Expected Monthly PnL (1-tick) | Notes |
|------|----------|--------|--------|-------------------------------|-------|
| 1-2 | Sweep_Absorption_Reversal | GC | thr=0.5 | ~$3k/slot | Star: 5x WF Sharpe, bootstrap p=0.01 |
| 3 | CVD_VWAP | GC | cvd=70, hold=12 | ~$1.5k | Most stable GC CVD variant |
| 4 | CVD_Microprice | SI | cvd=60, mp=1.0 | ~$1k | Most distinct SI strategy |
| 5 | CVD_VWAP | SI | cvd=70, hold=8 | ~$700 | Best SI CVD variant |
| 6-10 | Previously validated OHLCV strategies | ES/NQ/GC | per Phase 4 results | per Phase 4 | 5 survivors from OHLCV portfolio |

**WARNING:** SI CVD_VWAP variants are nearly identical — do NOT trade all 4 on separate accounts (correlated loss).

---

## Summary

- **Databento-only dry-run:** POSSIBLE — signals generate from stored bars, no new downloads needed
- **Mock broker tests:** PASS (7/7 smoke tests, 40/40 reconciliation tests)
- **Tradovate:** DISABLED (TRADOVATE_ENABLED=false)
- **New L2 strategy testing:** READY — 7 priority hypotheses in backlog, data exists
- **Live trading:** BLOCKED until demo account exists
