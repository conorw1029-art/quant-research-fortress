# Stop-Loss / Take-Profit / Breakeven Fill Analysis
**2026-06-28** — exactly how the system simulates exits today, and where the edge can improve.

## Where it happens
`RiskManager.update_bar(strat_id, bar_high, bar_low, bar_close)` is called **once per new bar**
for each open trade (`tick_live_executor.py:1295`). Entry is taken at the signal's price when the
signal fires; every subsequent bar is checked for an exit. Order of checks each bar:

| # | Check | Long fills when | Short fills when | Fill price |
|---|---|---|---|---|
| 1 | **Stop** (first) | `bar_low ≤ stop_px` | `bar_high ≥ stop_px` | `stop_px` |
| 2 | **Ratchet** +1.5R | `bar_high ≥ +1.5R` | `bar_low ≤ +1.5R` | stop → **+0.5R** (locks profit) |
| 2 | **Ratchet** +2.5R | `bar_high ≥ +2.5R` | `bar_low ≤ +2.5R` | stop → **+1.5R** |
| 3 | **Target (TP)** | `bar_high ≥ target_px` | `bar_low ≤ target_px` | `target_px` |
| 4 | **Timeout** | `bar_count ≥ 50` | same | `bar_close` |

**Defaults:** stop = 1.5×ATR, target = 3.0×ATR → **R:R = 2.0**. Ratchet 1.5R→lock0.5R, 2.5R→lock1.5R.
Max hold 50 bars. 1 contract (micros).

## Key facts
- ✅ **Stop is checked before target.** If one bar straddles both, the system assumes the **stop**
  filled first — the *conservative* (pessimistic) assumption. Good for realism.
- ✅ **Commission IS included:** ~$6 round-turn per contract (`commission=3.0`/side ×2).
- ❌ **Slippage is NOT modelled** — fills are at the *exact* stop/target price. Live fills slip
  ~1 tick, so current DRY_RUN P&L is **mildly optimistic** vs reality. → add ~1 tick slippage to be representative.
- ⚠️ **Ratchet uses bar extremes** (`bar_high` for longs). On a wide-range bar this assumes the
  favourable excursion happened *before* any reversal — an optimistic intra-bar assumption that
  slightly inflates the ratchet benefit. Only tick data can fully resolve it.

## Breakeven — what exists today
There is **no explicit "move stop to breakeven at +1R."** The ratchet jumps the stop from the
**initial stop (full 1R risk)** straight to **+0.5R lock at +1.5R**. So:
- From entry up to +1.5R → stop stays at full risk (no breakeven protection).
- `trail_to_breakeven=True` and "move to BE after partial" exist in config but only apply to the
  legacy **2+ contract partial** mode — **inactive in 1-contract ratchet mode**.
- Net: in live 1-contract mode, **no breakeven before +1.5R**.

## The edge opportunities (for the "most profitable version" optimization)
Per strategy, sweep and pick the variant that maximises net edge (Sharpe / profit factor / expectancy
after costs):
1. **STOP_MULT** (e.g. 1.0–2.5×ATR) and **TP_MULT** (1.5–4.0×ATR) → the R:R that fits each strategy.
2. **Breakeven trigger** — none vs move-to-BE at +0.75R / +1.0R. Early BE cuts losers but can also
   choke winners; must be tested per strategy, not assumed.
3. **Ratchet levels** — trigger/lock R-multiples, or replace with an ATR/chandelier trail.
4. **Timeout** — max_hold_bars sweet spot per strategy/timeframe.
5. Add **~1 tick slippage** to every fill so the optimizer ranks on realistic numbers.

Footprint strategies (V1/V10) get this optimization once live `trades` data is flowing; the OHLCV
strategies (V6/7/8/9) can be optimized now on existing data.
