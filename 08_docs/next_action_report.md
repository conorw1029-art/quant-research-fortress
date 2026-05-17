# Next Action Report — Fortress Trading System
**Date:** 2026-05-17  
**Based on:** live_readiness_audit.md + live_safety_checklist.md

---

## 1. Is the system safe to run in dry-run mode?

**YES — it is safe to run in dry-run mode right now.**

The executor defaults to DRY_RUN. No orders are placed. All signal decisions are logged to `06_live_trading/logs/signals_YYYYMMDD.jsonl`. The mode banner clearly labels every run. The kill switch (`KILL_SWITCH.txt`) is operational.

```
python tick_live_executor.py --poll 60 --alert-file alerts.json
```

Run this to watch signals and verify behaviour before connecting anything to a broker.

---

## 2. Is the system safe to connect to Tradovate demo?

**NO — not yet. Two hard blockers:**

**Blocker 1 — Bracket orders not implemented.**  
`place_bracket_order()` does not exist in `tick_tradovate_client.py`. The executor will refuse to start auto-trade mode and print a BLOCKED message. This is intentional. Without broker-native stops, positions would have no protection if the Python process crashes, which on a funded account could mean a total account loss.

**Blocker 2 — No account state persistence.**  
If the executor restarts mid-position (power cut, update, crash), the risk manager thinks it is flat and will attempt to re-enter positions that are already open at the broker. This creates duplicate positions and uncontrolled risk.

**Both must be fixed before demo auto-trading.**

---

## 3. Is bracket order support implemented?

**NO.** This is the next coding task. Tradovate supports bracket orders via:
- `POST /order/placeOSO` (Order Sends Order — entry fires the stop+target OCO)
- Or: two-step — send entry → on fill, send OCO stop+target

The safest approach is `placeOSO` which sends all three legs atomically. This needs to be added to `tick_tradovate_client.py` as `place_bracket_order(order, stop_price, target_price)`.

---

## 4. Are stops/targets broker-native or only internally tracked?

**Currently only internally tracked (in-memory).** The risk manager in `tick_risk_manager.py` tracks stop/target prices and simulates exits bar by bar. In dry-run and demo-disabled mode, no stop orders exist at the broker. This means if the process dies, the position is naked with no stop at the exchange. This is the primary safety blocker.

---

## 5. Are the 12 live strategies actually eligible, or should some be demoted?

**DEMOTE immediately (too risky for current account state — $1,000 remaining):**

| # | Strategy | Worst Day | Reason to Demote |
|---|----------|-----------|-----------------|
| 1 | GC/obi_threshold/1m | **$-9,647** | Single worst day > 4× remaining runway |
| 10 | GC/trade_absorption/30m | **$-4,486** | Single worst day > 2× remaining runway |
| 12 | NQ/trade_absorption/30m | $-3,234 | Only 21 trades — statistically meaningless |
| 5 | NQ/cvdlp/30m | $-4,824 | Only 81 trades AND worst day > 2× runway |

**Keep but watch closely:**

| # | Strategy | Concern |
|---|----------|---------|
| 11 | ES/avg_order_size/30m | Sharpe=1.03 (lowest) — weakest edge |
| 8 | NQ/range_contraction/30m | Trade count unknown — verify > 50 |
| 6 | NQ/stop_hunt/3m | 1 regime only |

**Eligible as-is for demo (all pass key criteria):**
- #2 ES/cvdlp/15m — Best starter: 100% TS, worst=$-3,827
- #3 ES/cvd_divergence/15m — Solid, all hours
- #4 ES/tape_absorption/15m — Asian+US only, clean
- #7 ES/prev_session_sweep/3m — 100% TS, all hours
- #9 GC/session_momentum/3m — Good date range, 100% TS

---

## 6. Which single strategy should be used first in demo?

**Strategy #2: ES/cvd_divergence_large_print/15m**

Rationale:
- 100% Topstep compliance in backtest
- Worst day $-3,827 (worst of the "safe" set but manageable)
- Tested on 5.5 months (same as all ES strategies)
- MES micro: risk ~$90–$150 per trade (well under $200)
- Most well-tested of the ES strategies (first backtest batch, deepest analysis)
- All hours except UTC 4,8,11,15,18,23 (already filtered in executor)

Run command (after Gates 6 and 7 pass):
```
python tick_live_executor.py --poll 60 --strategy 2 --demo-auto-trade \
  --username $env:TRADOVATE_USERNAME --password $env:TRADOVATE_PASSWORD \
  --cid $env:TRADOVATE_CID --secret $env:TRADOVATE_SECRET \
  --alert-file alerts.json --max-runtime-minutes 480
```

---

## 7. Which strategies should be disabled immediately due to worst-day risk?

**Disable NOW — remove from PORTFOLIO in executor or add to a disabled list:**

With only **$1,000 remaining per account**, any strategy whose worst historical day exceeds $1,000 can blow the account in a single session. The following strategies are **not eligible for the current account state**:

| # | Strategy | Worst Day | Action |
|---|----------|-----------|--------|
| 1 | GC/obi_threshold/1m | $-9,647 | DISABLE until account recovers to $5k+ |
| 5 | NQ/cvdlp/30m | $-4,824 | DISABLE — also only 81 trades |
| 6 | NQ/stop_hunt/3m | $-4,582 | DISABLE — worst day > $4k |
| 10 | GC/trade_absorption/30m | $-4,486 | DISABLE |
| 11 | ES/avg_order_size/30m | $-3,851 | DISABLE — weakest Sharpe AND large worst day |
| 12 | NQ/trade_absorption/30m | $-3,234 | DISABLE — 21 trades, not statistically valid |
| 8 | NQ/range_contraction/30m | $-3,439 | DISABLE until trade count confirmed |

**Safe for demo (worst day < $3k, reasonable trade count):**
- #2 ES/cvdlp/15m: $-3,827 (borderline — 1 micro MES = $-383 max, acceptable)
- #3 ES/cvd_divergence/15m: metrics incomplete, validate before enabling
- #4 ES/tape_absorption/15m: $-2,871 → MES = $-287 worst day
- #7 ES/prev_session_sweep/3m: $-2,813 → MES = $-281 worst day
- #9 GC/session_momentum/3m: $-3,042 → MGC = $-304 worst day

**Note:** All worst days above are for FULL contracts. With micro contracts (1/10 size), the micro worst days are $287–$383 on the safe set. These fit within the $1,000 remaining runway with room to recover.

---

## 8. What exact command should I run next?

**Right now — today:**
```powershell
# Run in dry-run to verify signals are firing
python tick_live_executor.py --poll 60 --quiet --alert-file alerts.json
```

Watch for a few hours. You should see signals firing for strategies #2, #3, #4, #7, #9. Review the signal log at `06_live_trading/logs/signals_20260517.jsonl`.

**Then — next coding session:**
1. Implement `place_bracket_order()` in `tick_tradovate_client.py` (Gate 6)
2. Implement startup reconciliation in the executor (Gate 7)
3. Disable the high-worst-day strategies from PORTFOLIO (strategies 1, 5, 6, 8, 10, 11, 12)
4. Add stale-data detection: warn if newest bar is >15 minutes old

**Then — after you have Tradovate credentials:**
1. Test REST bar builder: `python tick_bar_builder.py --rest --username ...`
2. Confirm new bars appear in `01_data/tick_bars/` with recent timestamps
3. Run dry-run executor on live bars and verify signals

**Then — after Gate 6 and Gate 7 pass:**
1. Run single-strategy demo for 1 week: `--strategy 2 --demo-auto-trade`
2. Review signal log and Tradovate demo positions daily

---

## 9. What should I avoid doing?

1. **Do not set `FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND` until Gates 3–11 are all PASS.** The funded accounts have $1,000 each remaining. One unchecked trade can end an account.

2. **Do not run strategies 1, 5, 6, 8, 10, 11, or 12 in any auto-trade mode.** Their worst days exceed your remaining runway. Strategy #12 has only 21 trades and its Sharpe is not statistically meaningful.

3. **Do not bypass the bracket order gate** by modifying `_has_bracket_orders()` to return True without actually implementing the method. The gate exists to protect you.

4. **Do not run multiple strategies in demo simultaneously** until single-strategy demo is proven. Multiple strategies on one account can stack worst days.

5. **Do not forget contract month rollover.** June 2025 contracts (MESM5, MGCM5, MNQM5) expire approximately June 20, 2026. Update `SYMBOL_MAP` in bar builder and `TV_CONTRACT_MAP` in executor at that time.

6. **Do not assume dry-run signals equal live performance.** The data ends May 14. If bar builder is not running, you are trading on 3-day-old bars. Verify data freshness before going to any live or demo mode.

7. **Do not run partial TP with 1 micro contract.** You cannot close 50% of 1 contract. Change `RISK_CFG` to set `partial_exit_r=None` or simply set `full_tp_r=3.0` and `trail_to_breakeven=False` for single-contract mode.

---

## Confirmed: Do not start demo trading until I explicitly approve.

This report documents the system state as of 2026-05-17. Gates 6 and 7 must pass before any order reaches a broker. Gates 3–5 and 8 must be manually tested and verified by you before approving demo trading.
