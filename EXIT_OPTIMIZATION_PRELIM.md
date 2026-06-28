# Exit Optimization — PRELIMINARY (2026-06-28)
Sweep of stop_mult × tp_mult × max_hold per strategy, GC & SI 15m, realistic slippage,
via the proven `run_backtest_slippage` engine. Output: `05_backtests/exit_optimization.json`.

## ⚠️ READ THIS FIRST — why these are preliminary
- **Only ~70 days of data** (Apr–Jun 2026). Far too short to trust; classic overfit territory.
- **$ figures are full-contract** (GC=$100/pt, SI=$5000/pt) — not the micros you'd trade. Magnitudes are illustrative only.
- Engine models **plain stop/TP/hold** — NOT the live ratchet or the new breakeven. So these are exit *geometry* hints, not drop-in live configs.
- **Do not deploy from this.** The real run is on the live `trades` footprint data once it flows.

## The only meaningful signal: cross-symbol consistency
7 of 29 strategies were **positive with Sharpe ≥ 0.4 on BOTH GC and SI** — a config that survives
on two different markets is less likely to be pure overfit:

| Strategy | GC best (stop×tp,hold) Sharpe | SI best (stop×tp,hold) Sharpe |
|---|---|---|
| **prior_day_hl_breakout** | 2.5×3.0 (50)  sh1.17 | 2.0×4.0 (50)  sh1.51 |
| **opening_range_breakout** | 2.5×3.0 (50)  sh1.16 | 2.5×3.0 (50)  sh1.56 |
| **bollinger_breakout** | 2.5×4.0 (80)  sh1.10 | 2.0×2.0 (80)  sh1.16 |
| **keltner_breakout** | 2.5×1.5 (80)  sh0.65 | 2.0×4.0 (50)  sh1.64 |
| **prior_day_hl_sweep** | 1.0×4.0 (30)  sh0.63 | 2.0×4.0 (30)  sh0.44 |
| **rolling_return_zscore** | 2.0×4.0 (30)  sh0.43 | 1.0×4.0 (50)  sh1.43 |
| **ma_slope_regime** | 2.0×4.0 (80)  sh0.94 | 1.0×4.0 (50)  sh0.42 |

## Patterns worth carrying into the real run
1. **Wider stops win.** Best configs cluster at **2.0–2.5×ATR stops** vs the live default 1.5×ATR.
2. **Wider targets win.** TP **3.0–4.0×ATR** dominates — let winners run.
3. **Breakout / structural strategies are the consistent ones** (prior-day H/L, opening range,
   bollinger/keltner breakout). Most **mean-reversion** strategies were negative or symbol-specific.
4. opening_range_breakout used the **same config (2.5×3.0, hold 50) on both** markets — the most robust single hint.

## Next (the real deliverable)
Re-run this on live `trades` footprint data + the RiskManager-faithful engine (ratchet + breakeven_r),
adding the breakeven dimension. Then the "most profitable version of each" is trustworthy.
