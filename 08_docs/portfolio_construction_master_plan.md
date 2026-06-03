# Portfolio Construction Master Plan

**Version**: 1.0  
**Date**: 2026-06-03  
**Author**: Fortress Research  
**Status**: Active

---

## 1. Why Individual Strategy Performance Does Not Equal Portfolio Performance

The most common mistake in systematic trading is treating strategy selection and portfolio construction as the same problem. They are not.

A strategy backtest answers: "Does this signal have positive expected value, net of costs, over 5 years?"

A portfolio backtest answers: "What happens to the trader's account equity when multiple strategies run simultaneously?"

These questions have very different answers, for three reasons:

### 1.1 Drawdown Concentration

Two strategies can each have a 10% maximum drawdown. If their drawdowns happen at the same time — during the same macro event, the same volatility spike, the same correlated market move — the combined account experiences a 20% drawdown on the same day. This is not additive in a safe sense; it is the worst-case scenario.

Running 5 uncorrelated strategies with 10% individual drawdowns does not guarantee 10% portfolio drawdown. It guarantees 10% portfolio drawdown only if the strategies are perfectly uncorrelated. In practice, correlations rise during crises — the exact time you can least afford it.

### 1.2 Net Position Risk

In a funded Topstep account, the account holds one net position per symbol. If Strategy A is long GC and Strategy B is also long GC at the same time, the account is effectively running 2 contracts of GC — doubling the exposure, doubling the drawdown per adverse tick, and potentially doubling the commission drag.

This is not a theoretical concern. In our GC evidence results, 48 strategies passed the evidence screen. Many of them signal simultaneously on the same bars. Running all 48 would produce a systematic 10-20 contract GC position on strong CVD days — far exceeding any funded account limit.

### 1.3 Sharpe Is Additive Under Independence, Not Otherwise

The portfolio Sharpe ratio is:

```
SR_portfolio = (sum of weighted strategy returns) / (portfolio return std dev)
```

If strategies are uncorrelated, `portfolio_std = sqrt(sum of weighted variance_i)`. This is much smaller than `sum of weighted std_i`, so the portfolio Sharpe exceeds any individual strategy Sharpe.

But if strategies are correlated at r = 0.8, `portfolio_std ≈ 0.9 × max(individual_std)`. Almost no diversification benefit. You have the same risk with more complexity.

The implication: **correlation is the primary portfolio construction variable, not individual Sharpe ratio**.

---

## 2. The 4 Portfolio Layers

The Fortress operates in 4 layers. A strategy must progress through each layer sequentially. No strategy goes live without surviving the earlier layers.

### Layer 1 — Research Layer

- **What**: Systematic strategy testing via the Strategy Testing Factory
- **Gate**: Evidence-grade status (DSR, WF validation, bootstrap p < 0.05, 2-tick slippage survival)
- **Output**: evidence-passed JSON files in `05_backtests/l2_results/`
- **Duration**: No minimum — testing is ongoing
- **Decision**: Each strategy is either rejected, watchlisted, or promoted to Layer 2

### Layer 2 — Manual Simulation Layer

- **What**: The researcher watches signals in real-time but does not execute. Signals are logged manually or via an alert system.
- **Gate**: 30+ signal events observed live, matching the backtest frequency (no substantial deviation)
- **Duration**: 1-3 months per strategy
- **Decision**: If live signal frequency matches backtest and qualitative assessment is positive, promote to Layer 3

### Layer 3 — Paper/Demo Trading Layer

- **What**: Paper trading on a Topstep demo account (or equivalent). No real capital at risk.
- **Gate**: 60+ trades completed, Sharpe on paper trades >= 1.0, drawdown within expected range
- **Duration**: 3-6 months
- **Decision**: Paper P&L within one sigma of backtest expectation → promote to Layer 4. Otherwise: re-examine, diagnose, and demote to Layer 1 for re-testing.

### Layer 4 — Live Trading Layer

- **What**: Active Topstep funded account, real capital at risk, ratchet stop active
- **Gate**: All pre-live gates passed (Monte Carlo, unit sizing, correlation check, account slot available)
- **Risk controls**: Daily loss limit, trailing max DD stop, PortfolioCoordinator net position enforcement
- **Ongoing**: Monthly performance reconciliation against backtest expectation (within 2 sigma)

---

## 3. Correlation Management Rules

The correlation threshold governs which strategies can coexist in the live portfolio.

### Rule 1 — The 0.70 Hard Ceiling

No two live strategies may have a daily P&L Pearson correlation exceeding 0.70 computed on the 5-year backtest period. This applies even if the strategies trade different signals, different families, or different parameter combinations.

Rationale: at r = 0.70, the portfolio variance reduction from combining two strategies is only:

```
variance_reduction = 1 - sqrt((1 + r) / 2) ≈ 1 - 0.93 = 7%
```

Seven percent variance reduction does not justify the operational complexity of running two strategies. The slot is better used for a genuinely uncorrelated strategy.

### Rule 2 — Same-Symbol Families

Strategies trading the same underlying (e.g., two GC strategies) receive additional scrutiny. Even with low correlation in their daily P&L, they may simultaneously hold positions in the same direction on high-conviction days, creating implicit correlation spikes.

Two strategies from the same symbol family are only allowed in the live portfolio simultaneously if:
- Their pairwise P&L correlation is below 0.50 (stricter than the general 0.70 threshold)
- Their signal overlap (fraction of bars where both have an active signal in the same direction) is below 30%
- The PortfolioCoordinator explicitly handles the net position conflict

### Rule 3 — Cross-Market Correlation Check

Gold (GC) and Silver (SI) have high structural correlation in volatility (both are precious metals, both react to the same macro drivers). When GC and SI strategies both signal long simultaneously during a precious metals rally, the account is exposed to the same underlying factor.

Before adding a new SI strategy when GC strategies are already live, compute the pairwise correlation between the new SI strategy's daily P&L and all existing GC live strategies. If any pair exceeds 0.70, the new SI strategy is placed in WATCHLIST — not necessarily rejected, but blocked until a GC strategy is removed.

### Rule 4 — Correlation Rises in Stress

Backtest correlations are computed on normal market periods. In tail events (2020 COVID, 2022 rate shock, geopolitical spikes), correlations typically converge toward 1.0 for positively correlated strategies. The portfolio manager must maintain a mental model of "stress correlation" that is approximately 20-30 percentage points higher than the backtest correlation.

Practical implication: if two strategies have a backtest correlation of 0.55, assume 0.75-0.80 during the next stress event. Manage accordingly.

---

## 4. Same-Symbol Conflict Resolution — PortfolioCoordinator Logic

The PortfolioCoordinator is the real-time component responsible for resolving conflicts when multiple strategies generate signals for the same symbol simultaneously.

### 4.1 Net Position Rule

An account may hold at most N contracts net in any direction for any symbol, where N is determined by the account's position limit (typically 1-3 contracts for a $50,000 Topstep account).

If Strategy A generates a long GC signal and Strategy B also generates a long GC signal in the same bar, and the account is already at its long limit, the PortfolioCoordinator must choose one of:

a. **Priority queue**: Execute the signal from the higher-DSR strategy. Queue the lower-DSR signal.
b. **Conflict suppression**: If both strategies have a signal overlap exceeding 30% in their backtest history, the lower-priority strategy's signal is suppressed — not queued, suppressed. The signal is logged for analysis.
c. **Scale suppression**: If there are M simultaneous signals and the account limit allows M/2 contracts, execute at half-size per signal (fractional contracts if the platform permits).

### 4.2 Directional Conflict

If Strategy A generates a long GC signal while Strategy B generates a short GC signal at the same bar, the strategies are in conflict:

- If the conflict is rare (< 5% of all signal bars in backtest), it is treated as noise. The higher-DSR strategy wins.
- If the conflict is frequent (>= 5% of signal bars), the strategies may be genuinely negatively correlated. This is valuable — allow both to proceed and let the position net to zero (effectively flat). Log the event.
- A strategy pair with frequent directional conflicts and negative pairwise correlation is actually desirable: they naturally hedge each other.

### 4.3 Position Sizing Hierarchy

Priority order when multiple strategies compete for the same position slot:

1. Highest WF Sharpe ratio (over the most recent 12-month OOS fold)
2. Lowest bootstrap p-value (strongest statistical evidence)
3. Longest consecutive months profitable (most stable recent performance)
4. Oldest deployment date (tie-breaker: established strategies get priority)

This hierarchy is re-computed monthly. A new strategy that has only 60 trades in live trading does not immediately displace an established strategy.

---

## 5. Drawdown Overlap Analysis and Why It Matters

The drawdown overlap metric measures the fraction of trading days where two strategies are simultaneously losing money from their respective equity peaks.

A simple example:
- Strategy A has maximum drawdown of $1,000
- Strategy B has maximum drawdown of $1,000
- If drawdown overlap = 0.0, their drawdowns never coincide. Portfolio max DD stays near $1,000.
- If drawdown overlap = 1.0, they always draw down together. Portfolio max DD is $2,000.

In practice, drawdown overlap is the single most important risk diversification metric. Two strategies with low P&L correlation can still have high drawdown overlap if they both tend to lose during the same market regimes (e.g., both strategies fail during low-volatility environments, even if their signal mechanics are completely different).

**Target drawdown overlap for any pair in the live portfolio**: < 0.30

Strategies with drawdown overlap above 0.50 are treated similarly to high-correlation strategies — they should not run simultaneously unless the researcher has a specific reason.

The portfolio optimizer computes a full drawdown overlap matrix using `tick_portfolio_optimizer.py --output-report`. This matrix is reviewed before any new strategy is added to the live portfolio.

---

## 6. Net Position Rules

### Rule: One Account, One Net Position per Symbol

Each Topstep funded account has a single account equity curve. Internally, multiple strategies may be active, but the broker sees only net contracts.

**Implication**: If the target portfolio is 5 strategies across 2 symbols (e.g., 3 GC strategies and 2 SI strategies), the maximum net position on GC is 3 contracts long or 3 contracts short. This has two consequences:

1. **Account position limit**: Most Topstep accounts have a position limit of 3-5 contracts. Running 3 GC strategies simultaneously stays within limit if they rarely all signal at once.
2. **Drawdown multiplication**: If all 3 GC strategies simultaneously enter a losing trade, the per-tick loss is 3× the single-contract loss. For a $50,000 GC account with $1,000 daily loss limit, 3 simultaneous 4-tick adverse moves would hit the limit.

### Rule: Position Accounting Before Signal Execution

Before any new signal is executed, the PortfolioCoordinator checks:
- Current net position in the symbol
- How many pending strategy signals are awaiting execution
- Whether executing the new signal would exceed the account's position limit

If adding the new position would exceed the limit, the signal is queued or suppressed per the conflict resolution rules in Section 4.

---

## 7. Recommended Starting Portfolio (5 Strategies from Evidence Results)

The recommended 5-strategy starting portfolio is selected by the minimum-correlation method from the evidence-passed results. All 5 strategies have passed the full 13-step evidence upgrade pipeline.

### Slot 1 — CVD_VWAP GC (Priority: HIGH)

| Metric | Value |
|--------|-------|
| Symbol | GC (Gold) |
| Parameters | vwap_band=0.5, cvd_pct=70, rr_ratio=2.0, hold_bars=8 |
| WF Sharpe | 4.00 (4 OOS folds) |
| WF Trades | 5,894 (OOS period) |
| Stress DSR | 0.40 |
| Bootstrap p | 0.00 |
| 2-tick Sharpe | 0.85 (PROFITABLE — 2-tick resilient) |
| 3-tick Sharpe | -0.29 (fails at 3 ticks) |
| Family | A_CVD |
| Slippage grade | 2-TICK |

**Thesis**: When cumulative volume delta is in the top 30th percentile and price is above the session VWAP band, there is systematic order flow alignment predicting continuation over the next 8 bars. The session VWAP filter dramatically reduces noise trades. Fails at 3 ticks, confirming it requires reasonable execution — appropriate for electronic GC on Globex.

**Signal overlap with other portfolio strategies**: To be computed live. Expected to be moderate with Repeated_Replenishment (both use CVD as a component).

### Slot 2 — Repeated_Replenishment GC (Priority: HIGH)

| Metric | Value |
|--------|-------|
| Symbol | GC (Gold) |
| Parameters | imbal_thr=0.2, persist_bars=3, cvd_negative=true, rr_ratio=2.0, hold_bars=8 |
| WF Sharpe | 3.87 (4 OOS folds) |
| WF Trades | 5,657 (OOS period) |
| Stress DSR | 0.28 |
| Bootstrap p | 0.00 |
| 2-tick Sharpe | 0.67 (PROFITABLE — 2-tick resilient) |
| 3-tick Sharpe | -0.53 (fails at 3 ticks) |
| Family | A_Absorption |
| Slippage grade | 2-TICK |

**Thesis**: Detects repeated order book replenishment at offer (aggressive sellers absorbing into bids) combined with negative CVD confirmation. This is a different mechanism from CVD_VWAP — it looks at the microstructure of bid replenishment rather than the aggregate CVD trend. Despite operating on GC, the signal source is distinct enough that pairwise correlation with CVD_VWAP should be below 0.70. Correlation must be verified before live deployment of both strategies simultaneously.

**Correlation monitoring requirement**: Before deploying with CVD_VWAP simultaneously, run `tick_portfolio_optimizer.py` and confirm CVD_VWAP vs Repeated_Replenishment correlation < 0.70.

### Slot 3 — CVD_Microprice SI (cvd=60, mp=1.0) (Priority: HIGH)

| Metric | Value |
|--------|-------|
| Symbol | SI (Silver) |
| Parameters | cvd_pct=60, mp_ticks=1.0, rr_ratio=2.0, hold_bars=5 |
| WF Sharpe | 2.35 (3 OOS folds) |
| WF Trades | 2,354+ (5yr total) |
| Stress DSR | 0.49 |
| Bootstrap p | 0.00 |
| 2-tick Sharpe | 0.93 (PROFITABLE) |
| 3-tick Sharpe | 0.20 (PROFITABLE — 3-tick resilient, gold standard) |
| Family | A_CVD |
| Slippage grade | 3-TICK (exceptional) |

**Thesis**: Dual confirmation from cumulative volume delta (top 40% of CVD distribution) and microprice deviation (order book mid tilted by >= 1.0 SI tick). The microprice filter is the key differentiator — it requires not just strong CVD but also the order book to physically lean in the signal direction. This combination produces a much lower trade count than using CVD alone, but with markedly higher signal quality. The 3-tick survival is the best in the evidence portfolio.

**Cross-market correlation risk**: This is a SI strategy. GC and SI are correlated. Monitor correlation against Slots 1 and 2 (both GC strategies). If GC/SI correlation has risen due to precious metals macro, the effective correlation between Slot 3 and Slots 1-2 will be higher than the backtest correlation.

### Slot 4 — Sweep_Continuation SI (Priority: MEDIUM)

| Metric | Value |
|--------|-------|
| Symbol | SI (Silver) |
| Parameters | min_sweeps=3, confirm_bars=2, rr_ratio=1.5, hold_bars=5 |
| WF Sharpe | 5.11 (3 OOS folds) — highest in evidence portfolio |
| WF Trades | 54 (OOS period) — ultra-selective |
| Stress DSR | 0.83 |
| Bootstrap p | 0.007 |
| 2-tick Sharpe | 1.58 (PROFITABLE) |
| 3-tick Sharpe | -0.65 (fails at 3 ticks) |
| Family | A_Sweep |
| Slippage grade | 2-TICK |

**Thesis**: Triggers only when 3+ aggressive sweep orders hit the book in rapid succession, confirming with a continuation bar. This is an ultra-selective strategy — approximately 75 trades over 5 years (about 15 per year). The extremely high WF Sharpe of 5.11 reflects the quality of the signal when it fires, not frequency. The low trade count (54 OOS trades across 3 folds) is a statistical risk: small sample size means the Sharpe estimate has high uncertainty. Monitor live carefully.

**Statistical caveat**: With ~15 trades per year, a 6-month monitoring period produces only ~7-8 trades. This is insufficient to distinguish a Sharpe of 5.1 from a Sharpe of 1.0 with any confidence. Deploy in paper trading first and accumulate 60+ trades before drawing conclusions about live performance.

### Slot 5 — CVD_Microprice SI (cvd=70, mp=1.0) (Conditional)

| Metric | Value |
|--------|-------|
| Symbol | SI (Silver) |
| Parameters | cvd_pct=70, mp_ticks=0.5, rr_ratio=2.0, hold_bars=5 |
| WF Sharpe | 2.63 (3 OOS folds) |
| WF Trades | 1,783 (OOS period) |
| Stress DSR | 0.19 |
| Bootstrap p | 0.00 |
| Family | A_CVD (same family as Slot 3) |

**Condition for inclusion**: This strategy is ONLY included in the portfolio if the CVD=60 variant (Slot 3) shows confirmed pairwise correlation > 0.70 with another already-deployed strategy. In that case, the CVD=60 variant is excluded and CVD=70 is evaluated as the replacement.

If CVD=60 correlation to other slots is below 0.70 (the expected outcome given it uses a different CVD threshold and full microprice tick filter), then Slot 5 is replaced by the next-best strategy from the evidence pool: OFI_Continuation GC (WF Sharpe 4.15, also 2-tick resilient).

**Why not both cvd=60 and cvd=70**: They share the same signal family, the same symbol, and highly overlapping parameter spaces. The pairwise correlation between them is expected to exceed 0.70. Two variants of the same strategy do not provide meaningful diversification and waste an account slot.

### Portfolio Summary Table

| Slot | Strategy | Symbol | WF Sharpe | Slippage Grade | Monthly Expected $ | Max DD Est. |
|------|----------|--------|-----------|----------------|-------------------|-------------|
| 1 | CVD_VWAP | GC | 4.00 | 2-tick | Positive | ~$3,000 |
| 2 | Repeated_Replenishment | GC | 3.87 | 2-tick | Positive | ~$2,800 |
| 3 | CVD_Microprice (cvd=60) | SI | 2.35 | 3-tick | Positive | ~$2,000 |
| 4 | Sweep_Continuation | SI | 5.11 | 2-tick | Low frequency | ~$800 |
| 5 | CVD_Microprice (cvd=70) | SI | 2.63 | 2-tick | Positive | ~$2,200 |

Note: Dollar estimates are approximations from stress-test P&L divided by 5-year window. Actual monthly P&L will vary. The expected_monthly values in `portfolio_candidate_report.json` are more precise.

### Account Allocation

With 10 funded Topstep accounts (~$1,000 DD per account) and a personal maximum DD of $2,000 per account:

- **Accounts 1-2**: CVD_VWAP GC only (establish baseline before adding complexity)
- **Accounts 3-4**: Repeated_Replenishment GC only (confirm correlation assumption live)
- **Account 5**: CVD_Microprice SI cvd=60 (learn SI execution characteristics)
- **Account 6**: Sweep_Continuation SI (ultra-low-frequency monitoring account)
- **Account 7**: CVD_Microprice SI cvd=70 OR OFI_Continuation GC depending on correlation test
- **Accounts 8-10**: Reserved for next cohort of evidence-grade strategies, or for scaling confirmed strategies

**Do not deploy all 5 strategies on the same account**. Each strategy should have its own account during the initial live period so performance can be isolated and attributed.

---

## 8. How to Add Strategies Over Time

Strategies are added to the live portfolio through a defined gate process. The gates are cumulative — all must pass.

### Gate 1 — Evidence Grade (already complete for recommended portfolio)

The strategy must pass the full 13-step evidence pipeline (see `strategy_testing_factory_design.md`). No exceptions.

### Gate 2 — Correlation Confirmation

Before adding a new strategy, run `tick_portfolio_optimizer.py` with the new strategy's daily P&L included. Confirm:
- All pairwise correlations with current live strategies < 0.70
- Drawdown overlap with all current live strategies < 0.30
- Portfolio Sharpe improves (positive `sharpe_improvement_over_best_individual`)

If correlation > 0.70 with any existing live strategy, the new strategy is placed in WATCHLIST. It is not added until an existing strategy is removed, or until additional backtest data demonstrates the correlation has decreased.

### Gate 3 — Paper Trading Confirmation

A minimum of 60 completed paper trades (or 3 months, whichever comes later). During this period:

- Signal frequency should match backtest frequency within ±30%
- No single paper trade loss should exceed 3× the average expected loss
- Cumulative paper P&L should be positive at the end of the paper trading period

### Gate 4 — Capacity Check

The addition must not push any symbol's total contracted exposure beyond the Topstep account position limit. With 1 contract per strategy and a 3-contract position limit per account, a maximum of 3 same-symbol strategies may be active per account.

### Gate 5 — Regime Check

Before adding a new strategy, review the current market regime. Adding a bullish-momentum strategy during a prolonged bear market may appear to fail live when in fact the backtest simply did not encounter this regime with sufficient frequency. Identify the regime in which the new strategy is weakest and be prepared to pause it during that regime.

### Gate 6 — Account Slot Availability

Topstep funded accounts are finite. If all 10 accounts are occupied by performing strategies, no new strategy can go live until:
- An existing strategy is decommissioned (3 consecutive months of live Sharpe < 0.5), OR
- An account is added, OR
- Two same-symbol strategies are consolidated onto a single account (only if correlation is confirmed < 0.50)

---

## 9. Current Top Candidates with Correlation Profile

The following table summarises the evidence-grade strategies available for portfolio consideration, with their estimated correlation profile based on signal family and symbol.

### Evidence Pool Summary

| Strategy | Symbol | WF Sharpe | Slippage | Trades/5yr | Portfolio Fit |
|----------|--------|-----------|----------|-----------|---------------|
| CVD_VWAP | GC | 4.00 | 2-tick | ~9,883 | Slot 1 anchor |
| Repeated_Replenishment | GC | 3.87 | 2-tick | ~10,008 | Slot 2 — verify correlation with Slot 1 |
| CVD_Microprice (cvd=60, mp=1.0) | SI | 2.35 | 3-tick | ~2,937 | Slot 3 — gold standard resilience |
| Sweep_Continuation (hold=5) | SI | 5.11 | 2-tick | ~75/5yr | Slot 4 — quality > quantity |
| CVD_Microprice (cvd=70, mp=0.5) | SI | 2.63 | 2-tick | ~3,747 | Slot 5 conditional |
| OFI_Continuation | GC | 4.15 | 2-tick | ~2,349 | Slot 5 alternate (lower trade count) |
| OFI_Microprice | GC | 4.26 | 2-tick | ~16,252 | High-frequency; correlation risk with OFI_Continuation |

### Estimated Correlation Groups

Based on signal family and symbol:

**Group A — GC CVD-family** (likely correlated with each other):
- CVD_VWAP GC
- CVD_Absorption GC (if evidence-grade)

**Group B — GC Absorption/Book** (likely lower correlation with Group A):
- Repeated_Replenishment GC
- OFI_Continuation GC (OFI is correlated with CVD directionally, but different signal source)

**Group C — SI CVD-family**:
- CVD_Microprice SI cvd=60
- CVD_Microprice SI cvd=70

**Group D — SI Sweep-family**:
- Sweep_Continuation SI
- Sweep_Absorption SI (if evidence-grade)

The recommended portfolio draws one strategy from each group (A or B, C, D) and adds the best cross-symbol strategy from the remaining groups. This is the structural reason why CVD_Microprice cvd=70 is listed as conditional: it would put two strategies from Group C into the portfolio, reducing diversification.

### Strategies Not Recommended

**Depth_Imbalance_Momentum**: Flagged for overlapping position issues. The strategy fires multiple signals per session on GC, creating position management complexity. Excluded from portfolio consideration until the position management logic is reviewed and a deconfliction wrapper is implemented.

**ES, NQ strategies**: Only 5.5 months of data available. Sample size is insufficient for the full evidence pipeline. All ES/NQ results are preliminary and not eligible for portfolio consideration until at least 3 years of clean data are available.

---

## 10. Ongoing Monitoring

### Monthly Reconciliation

At the end of each calendar month, compare live performance to backtest expectation for each active strategy. Using the backtest monthly P&L distribution:

- **Within 2 sigma**: Normal. No action required.
- **Between 2-3 sigma below expectation**: Elevated monitoring. Check if market regime has changed. Do not decommission yet.
- **More than 3 sigma below expectation for 3 consecutive months**: Decommission from live. Return to paper trading. Flag for re-testing with extended data.
- **More than 3 sigma above expectation**: Also investigate. Unexpected outperformance may indicate a regime change that will reverse, or a data/execution error.

### Correlation Drift

Re-compute the pairwise correlation matrix quarterly using live P&L data (replacing or supplementing the backtest data). If any pair's live correlation has risen above 0.70, decommission the lower-performing strategy of the pair until the correlation normalises or a root cause is identified.

### The Zoo Ledger Audit

Quarterly review of `05_backtests/research_ledger/zoo_research.jsonl` to identify:
- WATCHLIST strategies that have accumulated enough data for re-evaluation
- REJECTED strategies that were rejected due to insufficient data and may now have 3+ additional years of bars
- PAPER_CANDIDATE strategies that have completed their paper period and are ready for Gates 3-6

---

## Appendix A — Evidence Upgrade Criteria Reference

For a strategy to be promoted from evidence-grade to PAPER_CANDIDATE:

| Criterion | Required threshold |
|-----------|-------------------|
| WF Sharpe | >= 1.5 (no fold below 0.0) |
| DSR (Bailey 2014) | >= 0.95 |
| Bootstrap p-value | < 0.05 |
| Slippage survival | Profitable at 2 ticks |
| Regime breadth | Positive Sharpe in >= 3 of 5 regimes |
| Trade count (5yr OOS) | >= 200 |
| Portfolio correlation | All pairs < 0.70 |
| Drawdown overlap | All pairs < 0.30 |

---

## Appendix B — PortfolioCoordinator Decision Tree

```
New signal received for symbol S, direction D
    │
    ├── Is S at position limit already?
    │       No → Execute
    │       Yes → Is D opposite to current position?
    │               Yes → Reduce position (close hedge)
    │               No → Conflict resolution
    │                       Priority queue check
    │                       Suppress lower-priority signal
    │                       Log suppression to conflict_log.jsonl
    │
    └── Is D opposite to any active strategy signal for S?
            No → Execute
            Yes → Log directional conflict
                  Frequency check: > 5% of historical bars?
                    Yes → Allow both (net to flat)
                    No → Higher-DSR strategy wins
```
