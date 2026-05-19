# Trade Count Update Audit

**Date**: 2026-05-19  
**Status**: AUDIT COMPLETE — several counts marked UNVERIFIED

---

## 1. Which Files Were Changed by the Trade Count Update

The trade count update ran after commit `cfb3a97` (V678 integration). It targeted `live_strategy_allowlist.yaml`, filling all `trade_count: null` fields for strategies 1–15 and populating counts for strategies 16–38 from WFO JSON results.

Files modified:
- `04_codebase/live_strategy_allowlist.yaml` — trade_count fields updated for IDs 1–38

---

## 2. Source Breakdown by Strategy Group

### V6/V7/V8 Strategies (IDs 16–38) — WFO OOS n_trades — VERIFIED

These strategies were run through the WFO runner (`tick_runner_v678.py`) which uses walk-forward optimization. The `n_trades` field in the WFO JSON output records the number of **completed round-trip trades** in the OOS windows.

File: `05_backtests/tick_results_v678_20260518_1517.json`

These counts are reliable. They represent actual simulated round-trip trade completions, not signal transitions.

| ID  | Strategy Key                        | Count | Source        | Verified |
|-----|-------------------------------------|-------|---------------|----------|
| 16  | GC/vwap_mean_reversion/30m          | (from WFO JSON) | WFO OOS n_trades | YES |
| 17  | GC/pivot_reversal/30m               | (from WFO JSON) | WFO OOS n_trades | YES |
| 18  | SI/opening_range_fakeout/30m        | (from WFO JSON) | WFO OOS n_trades | YES |
| 19  | SI/consecutive_close_momentum/3m    | (from WFO JSON) | WFO OOS n_trades | YES |
| 20  | GC/pivot_reversal/15m               | (from WFO JSON) | WFO OOS n_trades | YES |
| 21  | SI/ema_crossover/1m                 | (from WFO JSON) | WFO OOS n_trades | YES |
| 22  | SI/vwap_mean_reversion/15m          | (from WFO JSON) | WFO OOS n_trades | YES |
| 23  | SI/opening_range_fakeout/3m         | (from WFO JSON) | WFO OOS n_trades | YES |
| 24–38 | (disabled / review)              | (from WFO JSON) | WFO OOS n_trades | YES |

---

### V1–V5 Strategies (IDs 1–15) — Signal Transition Counts — UNVERIFIED

For V1–V5 strategies, trade counts were computed using a signal-transition counting method:

```python
arr  = np.asarray(signal_series)
prev = np.roll(arr, 1); prev[0] = 0
count = int(np.sum((arr != 0) & (prev == 0)))
```

**This counts signal onset events — transitions from 0 to a non-zero value.** It is NOT a completed-trade count. Specifically:

- It counts every bar where a signal first fires, regardless of whether the prior position was already open.
- It does not confirm that each transition resulted in an executed order.
- It does not count exits. A strategy that entered 509 times may have had far fewer round-trip completions.
- If the signal flips from +1 to -1 without going through 0, a transition is not counted — but such a reversal would still generate an alert.

---

## 3. Counts From WFO JSON

The following IDs had their counts sourced directly from WFO OOS `n_trades`:

- **IDs 16–38** (all V6/V7/V8 survivors and disabled strategies)

These are reliable for deployment eligibility purposes.

---

## 4. Counts From Recomputed Signal Transitions

The following IDs had counts computed via the np.roll signal transition method:

- **ID 2** (ES/cvd_divergence_large_print/15m): 509 — SIGNAL COUNT, not completed trades
- **ID 3** (ES/cvd_divergence/15m): 624 — SIGNAL COUNT
- **ID 4** (ES/tape_absorption/15m): 508 — SIGNAL COUNT
- **ID 5** (NQ/cvd_divergence_large_print/30m): 81 — SIGNAL COUNT
- **ID 7** (ES/prev_session_sweep/3m): 909 — SIGNAL COUNT
- **ID 9** (GC/session_momentum_follow/3m): 295 — SIGNAL COUNT

These counts appear large and may give a false impression of statistical robustness. They are not equivalent to the WFO `n_trades` counts used for V678.

---

## 5. Computations That Errored or Timed Out

The original trade count update session experienced:

- **Timeout**: Full portfolio computation timed out before completing all strategies.
- **KeyError on strategy map access**: V1–V5 `STRATEGY_MAP` values are dicts `{"compute": fn, ...}`, not directly callable. Fixed by accessing `sd["compute"]` before calling.
- **Null counts for IDs 1, 6, 10, 11, 15**: These strategies either had no signal data, failed to compute, or the script reached its timeout before processing them. Their counts remain `null`.

---

## 6. Signal Counts vs Completed Trade Counts

| Concept | Definition | Used For |
|---|---|---|
| Signal transition count | Bars where signal fires for first time after being off | Rough activity proxy |
| Completed round-trip trade | Entry + exit pair with defined P&L | Deployment eligibility |
| WFO OOS n_trades | Round-trips counted in walk-forward OOS window | Reliable backtest metric |

**Signal counts should not be used as a proxy for trade count minimums in deployment eligibility rules.** A strategy with 509 signal transitions may have far fewer actual round-trip trades once open-position deduplication is applied.

---

## 7. Counts That Should Be Marked UNVERIFIED

The following strategy IDs have `trade_count` values derived from signal transitions, not completed round-trips. They should be treated as UNVERIFIED:

| ID | Key | Count | Issue |
|----|-----|-------|-------|
| 1  | GC/obi_threshold/1m | null | No count available |
| 2  | ES/cvd_divergence_large_print/15m | 509 | Signal transitions — not completed trades |
| 3  | ES/cvd_divergence/15m | 624 | Signal transitions |
| 4  | ES/tape_absorption/15m | 508 | Signal transitions |
| 5  | NQ/cvd_divergence_large_print/30m | 81 | Signal transitions |
| 6  | NQ/stop_hunt_reversal/3m | null | No count available |
| 7  | ES/prev_session_sweep/3m | 909 | Signal transitions |
| 9  | GC/session_momentum_follow/3m | 295 | Signal transitions |
| 10 | GC/trade_absorption_signal/30m | null | No count available |
| 11 | ES/avg_order_size_divergence/30m | null | No count available |
| 15 | GC/key_level_cvd_rejection/5m | null | No count available |

IDs 8, 12, 13, 14 (V3/V4/V5 stress-tested) have counts from stress test WFO runs — these are likely round-trip counts but should be confirmed.

---

## 8. Recommended New Fields for Allowlist

The allowlist should add the following fields to each strategy entry:

```yaml
trade_count_source: "WFO_OOS_N_TRADES" | "SIGNAL_TRANSITION_COUNT" | "MANUAL" | null
trade_count_verified: true | false
trade_count_method: "walk_forward_oos" | "np_roll_transition" | "backtest_ledger" | null
last_verified_at: "YYYY-MM-DD" | null
```

**Rule**: Any strategy with `trade_count_verified: false` must not be elevated from REVIEW_REQUIRED to DEMO_CANDIDATE until counts are verified via a proper backtest ledger or live signal log.

---

## 9. Deployment Eligibility Impact

No deployment classification is changed by this audit. The current allowlist already correctly blocks IDs 1–15 from DEMO_CANDIDATE status for other reasons (worst-day risk, regime checks, short history).

However: the `trade_count` fields for IDs 2–9 give a misleading impression of sample robustness. When these strategies are re-evaluated for promotion to DEMO_CANDIDATE, the trade counts must be verified from a proper backtest ledger, not from the signal transition script.

**Bottom line**: The high signal transition counts for V1–V5 strategies should not be interpreted as evidence of statistical robustness until verified against completed round-trip trade records.
