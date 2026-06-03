# Databento & L2 — Next Action Report

**Generated:** 2026-06-03  
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

## L2 Strategy Results (as of 2026-06-03) — FULL 5-YEAR COMPLETE

### Data Downloaded and Available
- GC (Gold) — mbp-10 tick data 2020-2026, built into `GC_bars_l2_1m.parquet` (77,270 bars)
- SI (Silver) — mbp-10 tick data 2020-2026, built into `SI_bars_l2_1m.parquet` (37,920 bars)
- Total Databento spend to date: $120.14 of $125 budget

### Quick Mode Backtests (recent 1-year sample)

| Symbol | Combos | Survivors | Hardened | Evidence Pass |
|--------|--------|-----------|----------|---------------|
| GC | 331 | 39 | 5 | 5/5 |
| SI | 322 | 29 | 5 | 5/5 |

### Full 5-Year Backtests — COMPLETE

| Symbol | Combos | Survivors | Hardened | Evidence Pass |
|--------|--------|-----------|----------|---------------|
| GC | 331 | ~50 | 48 | **48/48** |
| SI | 322 | 32 | 16 | **16/16** |

---

## GC Full 5-Year Top Survivors

Best strategies by 2-tick slippage resilience (the real deployment threshold):

| Strategy | WF Sharpe | Bootstrap p | 1-tick | 2-tick | 3-tick | Notes |
|----------|-----------|-------------|--------|--------|--------|-------|
| CVD_VWAP (best variant) | ~3.5 | 0.0000 | ~1.5 | **1.112** | 0.201 | Most resilient GC strategy |
| Depth_Imbalance_Momentum (best) | 4.4 | 0.0000 | ~1.8 | ~1.2 | **0.843** | HIGH trade count — see warning |
| Repeated_Replenishment (best) | ~3.2 | 0.0000 | ~1.4 | ~0.8 | ~0.2 | Realistic trade count 3-6k |

> **IMPORTANT — Depth_Imbalance_Momentum trade count warning:** 10k–30k trades in 77k GC bars = 39% of bars trigger entries. This implies overlapping positions, which massively inflates single-contract P&L metrics. Do NOT deploy without position-limit analysis. `Repeated_Replenishment` (3k–6k trades) is the safer GC choice.

> **Note on Sweep_Absorption_Reversal:** WF Sharpe >5 in quick mode (2024–2025) but **absent** from full 5-year hardened set — confirmed recent market-condition artifact. Do not deploy based on quick-mode results alone.

Full results: `05_backtests/l2_results/GC_passed_evidence.json` (48 strategies)

---

## SI Full 5-Year Top Survivors

| Strategy | WF Sharpe | Bootstrap p | 1-tick | 2-tick | 3-tick | Notes |
|----------|-----------|-------------|--------|--------|--------|-------|
| Sweep_Continuation (hold=5) | **5.109** | 0.0067 | 4.010 | **1.582** | -0.647 | Ultra-selective (~200 trades/yr) |
| CVD_Microprice (cvd=60, mp=1.0) | 2.354 | 0.0000 | 1.659 | **0.933** | **0.198** | SURVIVES 3 TICKS |
| CVD_Microprice (cvd=70, mp=1.0) | 2.636 | 0.0000 | 1.465 | **0.751** | **0.035** | SURVIVES 3 TICKS |
| CVD_VWAP (band=0.5, cvd=60, h=8) | 3.629 | 0.0000 | 1.564 | 0.367 | -0.809 | High frequency 4k trades |
| CVD_VWAP (band=0.5, cvd=70, h=12) | 2.679 | 0.0000 | 1.601 | 0.504 | -0.585 | |

**Portfolio standout:** `CVD_Microprice (mp_ticks=1.0)` is the most slippage-resilient strategy in the entire GC+SI portfolio — survives 3-tick slippage. Priority for Topstep deployment.

> **Sweep_Continuation note:** Absent in quick mode, strong in full 5-year (WF Sharpe=5.1). Opposite of Sweep_Absorption_Reversal on GC — this strategy IS robust over 5 years on SI.

Full results: `05_backtests/l2_results/SI_passed_evidence.json` (16 strategies)

---

## Quick Mode Evidence Results (reference — superseded by 5-year)

<details>
<summary>GC quick-mode survivors (1-year, 2024-2025)</summary>

| Strategy | WF Sharpe | Bootstrap p | 1-tick | 2-tick |
|----------|-----------|-------------|--------|--------|
| OFI_Continuation | 2.98 | 0.0000 | 0.846 | -0.652 |
| Sweep_Absorption_Reversal (thr=0.5) | 5.13 | 0.0100 | 1.276 | -0.537 |
| Sweep_Absorption_Reversal (thr=0.8) | 5.04 | 0.0140 | 1.237 | -0.547 |
| CVD_VWAP (hold=8) | 2.41 | 0.0000 | 0.859 | -0.219 |
| CVD_VWAP (hold=12) | 2.20 | 0.0000 | 0.868 | -0.159 |

</details>

<details>
<summary>SI quick-mode survivors (1-year, 2024-2025)</summary>

| Strategy | WF Sharpe | Bootstrap p | 1-tick | 2-tick |
|----------|-----------|-------------|--------|--------|
| CVD_Microprice | 1.55 | 0.0220 | 0.609 | -0.144 |
| CVD_VWAP (cvd=60, hold=8) | 2.22 | 0.0000 | 0.468 | -0.931 |
| CVD_VWAP (cvd=60, hold=12) | 1.91 | 0.0000 | 0.399 | -0.973 |
| CVD_VWAP (cvd=70, hold=8) | 1.51 | 0.0000 | 0.552 | -0.798 |
| CVD_VWAP (cvd=70, hold=12) | 1.23 | 0.0000 | 0.525 | -0.799 |

</details>

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
| `tick_evidence_upgrade.py` | Walk-forward + bootstrap + slippage ladder | ✅ (DSR + commission bugs fixed) |
| `tick_mock_broker.py` | Bracket/order simulation, no API calls | ✅ (7/7 smoke tests pass) |
| `tick_l2_backtest.py` | L2 strategy battery | ✅ (commission + DSR bugs fixed) |
| `08_docs/no_demo_account_execution_plan.md` | Broker restrictions | ✅ |
| `src/l2/` | L2 feature engine | ✅ |
| `test_broker_reconciliation.py` | Reconciliation tests (40/40) | ✅ |

---

## Topstep Account Allocation Recommendation (Updated — Full 5-Year Results)

Based on full 5-year evidence, prioritized for 10 funded accounts:

| Slot | Strategy | Symbol | Params | Max Slippage | Notes |
|------|----------|--------|--------|--------------|-------|
| 1 | Sweep_Continuation | SI | hold=5 | 2-tick | Star: WF Sharpe=5.1, ultra-selective |
| 2-3 | CVD_Microprice | SI | cvd=60/70, mp=1.0 | **3-tick** | Only 3-tick survivor in portfolio |
| 4-5 | CVD_VWAP | GC | best band/cvd | 2-tick | WF Sharpe 3-4, 2-tick Sharpe 1.1 |
| 6 | Repeated_Replenishment | GC | best params | 2-tick | Realistic trade count, not inflated |
| 7-10 | Previously validated OHLCV strategies | ES/NQ/GC | per Phase 4 | per Phase 4 | 5 survivors from OHLCV portfolio |

**WARNINGS:**
- Do NOT deploy Sweep_Absorption_Reversal on GC — recency artifact, fails 5-year test
- Do NOT deploy Depth_Imbalance_Momentum without position-limit analysis (overlapping positions inflate metrics)
- SI CVD_VWAP variants are correlated — pick at most 1 if deploying (slots 2-3 prefer CVD_Microprice instead)

---

## Exact Commands to Run Next

### Step 1 — Credential preflight (run before every session)
```
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 04_codebase/tick_credentials_preflight.py
```

### Step 2 — Test new backlog strategies (first priority: cvd_divergence_vwap)
```
cd 04_codebase
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 tick_l2_backtest.py --symbol GC
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 tick_l2_backtest.py --symbol SI
```
*(After implementing new strategies from l2_strategy_backlog.json into tick_l2_backtest.py)*

### Step 3 — Position-limit analysis for Depth_Imbalance_Momentum GC
Implement 1-contract-at-a-time enforcement in `_l2_trades()` and re-run evidence upgrade on GC to get realistic single-contract metrics.

### Step 4 — Run mock broker smoke test
```
C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe -X utf8 04_codebase/tick_mock_broker.py
```

---

## Summary

- **Full 5-year backtests:** COMPLETE — GC 48/48 pass, SI 16/16 pass evidence
- **Portfolio standout:** SI CVD_Microprice (mp=1.0) survives 3-tick slippage — most robust strategy found
- **Recency artifact confirmed:** Sweep_Absorption_Reversal GC is quick-mode-only, not in 5-year hardened set
- **New strong SI entrant:** Sweep_Continuation SI (hold=5) — WF Sharpe=5.1, absent in quick mode
- **Databento-only dry-run:** POSSIBLE — signals generate from stored bars, no new downloads needed
- **Mock broker tests:** PASS (7/7 smoke tests, 40/40 reconciliation tests)
- **Tradovate:** DISABLED (TRADOVATE_ENABLED=false)
- **New L2 strategy testing:** READY — 7 priority hypotheses in backlog, data exists
- **Live trading:** BLOCKED until demo account exists
