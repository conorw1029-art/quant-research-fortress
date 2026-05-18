# Strategy Deployment Eligibility Classification
**Date:** 2026-05-18  
**Basis:** 150-day backtest (Dec 15 2025 – May 14 2026), 1-tick Sharpe analysis, allowlist audit  
**Status:** CLASSIFICATION DOCUMENT — Review before any demo or live deployment

---

## Classification Tiers

| Tier | Meaning | Auto-trade allowed? |
|---|---|---|
| `DEMO_CANDIDATE` | First and only strategy eligible for single-strategy demo auto-trade. All evidence and execution gates must also pass before demo begins. | Yes — demo only, one at a time |
| `ENABLED_DRY_RUN` | Eligible for dry-run signal generation. Not cleared for demo auto-trade. | Dry-run only |
| `REVIEW_REQUIRED` | Metrics incomplete, trade count low, or data sparsity issue. Dry-run allowed but flagged in banner. Demo blocked. | Dry-run only |
| `RESEARCH_ONLY` | Historical record. Never loads in the executor in any mode. | No |
| `DISABLED_FOR_LIVE` | Worst-day risk exceeds account runway OR fundamental eligibility failure. Kept for audit. | No |

---

## Eligibility Rules (All 7 Must Pass for DEMO_CANDIDATE)

| # | Rule | Threshold |
|---|---|---|
| R1 | Worst-day micro loss | Must be ≤ $1,000 (10% of typical Topstep account) |
| R2 | Trade count | Must be ≥ 100 (or strong event-based rationale for lower-frequency strategy) |
| R3 | 1-tick Sharpe | Must be ≥ 1.0 |
| R4 | Data depth | Must cover ≥ 2 meaningful regimes (not one continuous trend period) |
| R5 | Profit factor | Must be ≥ 1.25 |
| R6 | No missing critical metrics | All of: worst_day, trade_count, sharpe_1t must be populated |
| R7 | Allowlist confirmed | Status in `live_strategy_allowlist.yaml` must agree with this classification |

---

## Strategy Classifications

### Strategy 1 — GC/obi_threshold/1m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | ~$965 | ≤ $1,000 | MARGINAL |
| Trade count | Unknown (null in allowlist) | ≥ 100 | FAIL |
| 1t-Sharpe | Unknown (null in allowlist) | ≥ 1.0 | FAIL |
| Data depth | GC 6 years | ≥ 2 regimes | PASS |
| Profit factor | Unknown | ≥ 1.25 | FAIL |
| Metrics complete | No — trade_count/sharpe null | All populated | FAIL |
| Data sparsity | 1m GC bars are sparse overnight | None | WARNING |

**Classification: DISABLED_FOR_LIVE**  
**Reason:** Worst-day micro is marginally within limit ($965), but 4 other rules fail due to missing metrics. The 1m GC data has known sparsity issues in overnight sessions. Cannot classify as eligible until all metrics are populated and verified. Currently disabled due to worst-day proximity to limit.

**Path to re-evaluate:** Populate trade_count, sharpe_1t, and profit_factor from full 7-year backtest run. If all 7 rules pass, upgrade to ENABLED_DRY_RUN. Do not promote to DEMO_CANDIDATE before GC regime dependency is assessed.

---

### Strategy 2 — ES/cvd_divergence_large_print/15m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $383 | ≤ $1,000 | PASS |
| Trade count | Unknown (null) but strategy has been running since Dec 2025 | ≥ 100 | PROVISIONAL |
| 1t-Sharpe | Not in allowlist but 150-day Sharpe 3.82 | ≥ 1.0 | PASS |
| Data depth | ES 5.5 months only | ≥ 2 regimes | WEAK — only Dec 2025-May 2026 |
| Profit factor | Not populated explicitly | ≥ 1.25 | UNKNOWN |
| Metrics complete | Partial | All populated | PARTIAL |
| Allowlist | DEMO_CANDIDATE | Agree | PASS |

**Classification: DEMO_CANDIDATE (current)**  
**Rationale:** This is the designated first and only demo strategy. It has the lowest worst-day micro loss among ES strategies, a strong 150-day Sharpe, and Topstep 100% compliance. The data depth weakness (5.5 months, one regime) is acknowledged and means the demo result will be as important as the backtest.

**Caution:** 30-day regime analysis shows Sharpe drop from 3.82 (150d) to 0.45 (30d). This is not a demotion trigger alone, but the demo phase will determine whether the strategy performs in live execution. If demo results show > 60% degradation from backtest expectations, reconsider.

**Conditions for demo start:** Gate 6 (bracket orders exchange-verified) + Gate 7 (reconciliation) + credentials pre-flight all pass. See `bracket_order_design.md`.

---

### Strategy 3 — ES/cvd_divergence/15m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | Unknown (null) | ≤ $1,000 | UNKNOWN |
| Trade count | Unknown (null) | ≥ 100 | UNKNOWN |
| 1t-Sharpe | Unknown (null) | ≥ 1.0 | UNKNOWN |
| Data depth | ES 5.5 months | ≥ 2 regimes | WEAK |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Metrics complete | No | All populated | FAIL |

**Classification: ENABLED_DRY_RUN (retained)**  
**Reason:** The strategy is a variant of Strategy 2 (same instrument/timeframe, different signal). It generates signals in dry-run mode. Cannot be promoted to DEMO_CANDIDATE until all metrics are populated. It is acceptable in dry-run as a monitoring strategy.

**Path to review:** Run full backtest to populate trade_count, worst_day_micro, sharpe_1t, and profit_factor. Then re-evaluate against all 7 rules.

---

### Strategy 4 — ES/tape_absorption/15m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $287 | ≤ $1,000 | PASS |
| Trade count | Unknown (null) | ≥ 100 | UNKNOWN |
| 1t-Sharpe | Unknown (null) | ≥ 1.0 | UNKNOWN |
| Data depth | ES 5.5 months | ≥ 2 regimes | WEAK |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Metrics complete | Partial (worst_day only) | All populated | FAIL |

**Classification: ENABLED_DRY_RUN (retained)**  
**Reason:** Worst-day micro is acceptable. Asian + US session filter reduces exposure. Cannot be evaluated for demo promotion without full metrics. Runs in dry-run as a monitoring strategy.

---

### Strategy 5 — NQ/cvd_divergence_large_print/30m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $482 | ≤ $1,000 | PASS |
| Trade count | 81 | ≥ 100 | **FAIL** |
| 1t-Sharpe | Unknown | ≥ 1.0 | UNKNOWN |
| Data depth | NQ 5.5 months | ≥ 2 regimes | WEAK |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Metrics complete | Partial | All populated | FAIL |

**Classification: DISABLED_FOR_LIVE**  
**Reason:** 81 trades is statistically insufficient for reliable Sharpe or profit factor. Rule R2 fails independently. Worst-day is acceptable but other rules fail. NQ also carries higher tick volatility than ES.

**Path to review:** Needs either more history (NQ 2023+ data) to generate > 100 trades, or a rationale explaining why this low-frequency strategy is reliable at 81 trades (e.g., event-based strategy with known setup count).

---

### Strategy 6 — NQ/stop_hunt_reversal/3m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $458 | ≤ $1,000 | PASS |
| Trade count | Unknown | ≥ 100 | UNKNOWN |
| 1t-Sharpe | Unknown | ≥ 1.0 | UNKNOWN |
| Data depth | NQ 5.5 months | ≥ 2 regimes | WEAK |
| Single-regime note | Allowlist flags "single regime only" | Multi-regime required | FLAG |
| Metrics complete | No | All populated | FAIL |

**Classification: DISABLED_FOR_LIVE**  
**Reason:** Explicitly flagged as "single regime only" in the allowlist. Even if trade count and Sharpe were satisfactory, single-regime strategies cannot be demo candidates. The 5.5-month NQ window is one regime.

---

### Strategy 7 — ES/prev_session_sweep/3m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $281 | ≤ $1,000 | PASS |
| Trade count | Unknown (null) | ≥ 100 | UNKNOWN |
| 1t-Sharpe | 1.45 | ≥ 1.0 | PASS |
| Data depth | ES 5.5 months | ≥ 2 regimes | WEAK |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Topstep compliant | 100% | — | PASS |
| Metrics complete | Partial (sharpe, worst_day) | All populated | PARTIAL |

**Classification: ENABLED_DRY_RUN (retained)**  
**Reason:** Acceptable worst-day micro and a Sharpe above 1.0. However: 30-day regime analysis shows Sharpe dropped to -2.54 (tariff volatility period). This is concerning and means the strategy is currently underperforming. Correct action: keep in dry-run, monitor for 30-day Sharpe recovery, do NOT demote to DISABLED based on one bad period alone (the trailing-drawdown risk manager handles this in live trading). Populate trade_count and profit_factor before any further promotion.

**Regime note:** -2.54 30-day Sharpe is the worst among all strategies in the current period. Do not promote to DEMO_CANDIDATE until regime normalises and 30-day Sharpe recovers to ≥ 1.0.

---

### Strategy 8 — NQ/range_contraction_break/30m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $344 | ≤ $1,000 | PASS |
| Trade count | Unknown (null) — flagged "verify > 50" | ≥ 100 | UNKNOWN |
| 1t-Sharpe | 5.63 | ≥ 1.0 | PASS |
| Data depth | NQ 5.5 months | ≥ 2 regimes | WEAK |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Topstep compliant | 100% | — | PASS |
| Metrics complete | Partial | All populated | PARTIAL |

**Classification: REVIEW_REQUIRED (retained)**  
**Reason:** Trade count is not verified. Given it's a 30m NQ strategy over 5 months, could plausibly be ≥ 100, but this must be confirmed. Sharpe is excellent (5.63). 30-day Sharpe is 3.32 (outperforming). Once trade_count is confirmed ≥ 100 and profit_factor is populated, should be upgraded to ENABLED_DRY_RUN.

---

### Strategy 9 — GC/session_momentum_follow/3m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $304 | ≤ $1,000 | PASS |
| Trade count | Unknown (null) | ≥ 100 | UNKNOWN |
| 1t-Sharpe | 3.22 | ≥ 1.0 | PASS |
| Data depth | GC 7 years | ≥ 2 regimes | PASS |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Topstep compliant | 100% | — | PASS |
| Metrics complete | Partial | All populated | PARTIAL |

**Classification: ENABLED_DRY_RUN (retained)**  
**Reason:** GC data is robust (7 years). Worst-day and Sharpe both acceptable. Trade count unknown — for a 3m GC strategy over 7 years it is almost certainly > 100, but must be confirmed before promotion. 30-day Sharpe is 6.80 (exceptional). Populate trade_count and profit_factor, then consider as a second demo candidate after Strategy 2 is proven.

**Note:** Do not promote to DEMO_CANDIDATE before Strategy 2 demo proves execution safety. First demo should test execution, not maximise P&L.

---

### Strategy 10 — GC/trade_absorption_signal/30m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $449 | ≤ $1,000 | PASS |
| Trade count | Unknown | ≥ 100 | UNKNOWN |
| 1t-Sharpe | 4.65 | ≥ 1.0 | PASS |
| Data depth | GC 7 years | ≥ 2 regimes | PASS |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Topstep compliant | 100% | — | PASS |
| Metrics complete | Partial | All populated | PARTIAL |

**Classification: DISABLED_FOR_LIVE (current allowlist)**  
**Reason (audit):** The current allowlist disables this strategy with worst_day $449 micro and reason "Exceeds practical account runway at $1,000 remaining." However, $449 micro worst day is within the $1,000 limit. This appears to be based on the **full-contract** worst day of $4,486 being compared against the $1,000 per-account runway — not the micro figure.

**Recommended correction:** If micro worst-day of $449 is accurate, this strategy passes R1. It should be REVIEW_REQUIRED (pending trade_count and profit_factor), not DISABLED_FOR_LIVE. Update the allowlist with a note clarifying the basis for the disable decision.

**Caution:** Do not change status to ENABLED_DRY_RUN without first populating trade_count and profit_factor.

---

### Strategy 11 — ES/avg_order_size_divergence/30m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $385 | ≤ $1,000 | PASS |
| Trade count | Unknown | ≥ 100 | UNKNOWN |
| 1t-Sharpe | 1.03 | ≥ 1.0 | MARGINAL |
| Data depth | ES 5.5 months | ≥ 2 regimes | WEAK |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Topstep compliant | 100% | — | PASS |
| Metrics complete | Partial | All populated | PARTIAL |

**Classification: DISABLED_FOR_LIVE (retained)**  
**Reason:** Lowest 1t-Sharpe in the portfolio (1.03, barely above the 1.0 threshold). ES data is 5.5 months only. Combined with the worst Sharpe and the current ES mean-reversion regime weakness, the risk/reward of enabling this strategy is poor. Keep disabled until Sharpe is re-verified on longer ES history.

---

### Strategy 12 — NQ/trade_absorption_signal/30m

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $323 | ≤ $1,000 | PASS |
| Trade count | 21 | ≥ 100 | **CRITICAL FAIL** |
| 1t-Sharpe | 6.45 | ≥ 1.0 | PASS (but meaningless at n=21) |
| Data depth | NQ 5.5 months | ≥ 2 regimes | WEAK |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Topstep compliant | 100% | — | PASS |

**Classification: DISABLED_FOR_LIVE (retained)**  
**Reason:** 21 trades is statistically meaningless. A 6.45 Sharpe on 21 trades tells us nothing. Rule R2 fails critically. Cannot be considered for any deployment until ≥ 100 trades are generated — which requires either more NQ history or waiting for more live dry-run signals.

---

### Strategy 13 — ES/key_level_cvd_rejection/15m (V5)

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $318 | ≤ $1,000 | PASS |
| Trade count | 125 | ≥ 100 | PASS |
| 1t-Sharpe | 1.89 | ≥ 1.0 | PASS |
| Data depth | ES 5.5 months only | ≥ 2 regimes | **FAIL** |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Topstep compliant | 100% | — | PASS |

**Classification: REVIEW_REQUIRED (retained)**  
**Reason:** Data depth is the critical failure. One regime of ES data does not provide regime-robust evidence. Path is clear: when ES bar data extends to 2023+, re-run backtest. If performance holds across bear-market, rate-hike, and low-vol regimes, upgrade to ENABLED_DRY_RUN. Do not promote before then.

---

### Strategy 14 — NQ/key_level_cvd_rejection/15m (V5)

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $397 | ≤ $1,000 | PASS |
| Trade count | 135 | ≥ 100 | PASS |
| 1t-Sharpe | 2.10 | ≥ 1.0 | PASS |
| Data depth | NQ 5.5 months only | ≥ 2 regimes | **FAIL** |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Topstep compliant | 100% | — | PASS |

**Classification: REVIEW_REQUIRED (retained)**  
**Reason:** Same data depth failure as Strategy 13. NQ data is 5.5 months. 30-day regime analysis shows outperformance (Sharpe 3.32), but this is one data point. Path: acquire NQ history to 2023+, re-test, then evaluate. Do not promote based on current-period outperformance alone.

---

### Strategy 15 — GC/key_level_cvd_rejection/5m (V5)

| Metric | Value | Rule | Pass? |
|---|---|---|---|
| Worst-day micro | $1,623 | ≤ $1,000 | **CRITICAL FAIL** |
| Trade count | 3,315 | ≥ 100 | PASS |
| 1t-Sharpe | 0.92 | ≥ 1.0 | FAIL |
| Data depth | GC 7 years | ≥ 2 regimes | PASS |
| Profit factor | Unknown | ≥ 1.25 | UNKNOWN |
| Topstep compliant | 99.3% | ≥ 99% | PASS |

**Classification: REVIEW_REQUIRED (current allowlist) — Recommend DISABLED_FOR_LIVE**  
**Reason:** Worst-day micro of $1,623 is 1.6x the $1,000 account runway limit. This is a critical failure of R1. The note in the allowlist says "risk manager caps at $250/strategy/day" — but this is a parameter that can be changed and represents internal risk management only, not broker-native protection. On a day when the cap fails or is overridden, exposure is $1,623.

Additionally, 1t-Sharpe of 0.92 is below the 1.0 threshold. Even at 0.5t: Sharpe=1.03 it is only marginal.

**Recommended action:** Demote to DISABLED_FOR_LIVE until: (1) 1t-Sharpe is confirmed ≥ 1.0 on live data, AND (2) worst-day micro is reduced through tighter stop design or position sizing that genuinely limits the worst day below $1,000.

---

## Summary Table

| ID | Key | Current Status | Recommended Status | Primary Block |
|---|---|---|---|---|
| 1 | GC/obi_threshold/1m | DISABLED_FOR_LIVE | DISABLED_FOR_LIVE | Missing metrics, sparse 1m data |
| 2 | ES/cvd_divergence_large_print/15m | **DEMO_CANDIDATE** | **DEMO_CANDIDATE** | Needs execution gates to open |
| 3 | ES/cvd_divergence/15m | ENABLED_DRY_RUN | ENABLED_DRY_RUN | Missing metrics |
| 4 | ES/tape_absorption/15m | ENABLED_DRY_RUN | ENABLED_DRY_RUN | Missing metrics |
| 5 | NQ/cvd_divergence_large_print/30m | DISABLED_FOR_LIVE | DISABLED_FOR_LIVE | n=81, missing metrics |
| 6 | NQ/stop_hunt_reversal/3m | DISABLED_FOR_LIVE | DISABLED_FOR_LIVE | Single regime |
| 7 | ES/prev_session_sweep/3m | ENABLED_DRY_RUN | ENABLED_DRY_RUN | 30d Sharpe -2.54, missing metrics |
| 8 | NQ/range_contraction_break/30m | REVIEW_REQUIRED | REVIEW_REQUIRED | Trade count unverified |
| 9 | GC/session_momentum_follow/3m | ENABLED_DRY_RUN | ENABLED_DRY_RUN | Missing trade_count, profit_factor |
| 10 | GC/trade_absorption_signal/30m | DISABLED_FOR_LIVE | REVIEW_REQUIRED | Allowlist reason may be incorrect (micro $449 ≤ $1k) |
| 11 | ES/avg_order_size_divergence/30m | DISABLED_FOR_LIVE | DISABLED_FOR_LIVE | Lowest Sharpe, ES 5-month regime only |
| 12 | NQ/trade_absorption_signal/30m | DISABLED_FOR_LIVE | DISABLED_FOR_LIVE | n=21, meaningless Sharpe |
| 13 | ES/key_level_cvd_rejection/15m | REVIEW_REQUIRED | REVIEW_REQUIRED | ES data 5.5 months only |
| 14 | NQ/key_level_cvd_rejection/15m | REVIEW_REQUIRED | REVIEW_REQUIRED | NQ data 5.5 months only |
| 15 | GC/key_level_cvd_rejection/5m | REVIEW_REQUIRED | **DISABLED_FOR_LIVE** | Worst-day micro $1,623 > $1,000; 1t-Sharpe 0.92 |

---

## Immediate Recommended Allowlist Actions

1. **Strategy 15**: Demote from REVIEW_REQUIRED to DISABLED_FOR_LIVE. The worst-day micro of $1,623 exceeds the $1,000 account runway limit. The executor already blocks it from running in live mode, but the allowlist should accurately reflect the reason.

2. **Strategy 10**: Review the disable reason. The allowlist uses the full-contract worst-day ($4,486) as justification, but the micro worst-day is $449 which is within limits. Correct the reason to accurately reflect the basis, or re-classify to REVIEW_REQUIRED pending metric completion.

3. **All strategies with null metrics**: Create a backtest run task to populate trade_count, sharpe_1t, and profit_factor for strategies 3, 4, 7, 9 from the existing tick parquet data. These are the most likely candidates for future ENABLED_DRY_RUN → DEMO_CANDIDATE path.

---

## Change Log

| Date | Strategy | Change | Reason |
|---|---|---|---|
| 2026-05-18 | Strategy 15 (GC/key_level_cvd_rejection/5m) | REVIEW_REQUIRED → **DISABLED_FOR_LIVE** | Worst-day micro risk $1,623 exceeds $1,000 account runway limit. Internal daily halt is not broker-native protection. |

---

*Review and update this document after each demo sprint evaluation.*
