# Evidence Upgrade Plan

**Status**: Planning  
**Purpose**: Systematic plan to raise statistical confidence before demo trading begins.

---

## 1. Purpose

The current backtest evidence base is insufficient for trusting the system with real capital — even on a funded demo account. The goal of this plan is to close the gaps between "backtest looked good" and "I have solid statistical evidence this strategy generalises."

Specific improvements needed:
- Expand ES/NQ data from 5 months to 2+ years
- Add formal overfitting tests (PBO, CSCV, White Reality Check)
- Slice performance by market regime, not just overall metrics
- Stress test execution assumptions (slippage, latency, missed fills)
- Define explicit demo-eligibility gates before any strategy goes to demo

---

## 2. Current Data Situation

| Symbol | Available History | Coverage Gaps |
|--------|------------------|---------------|
| GC (Gold futures) | ~6 years (2020–2026) | Solid — multiple regimes represented |
| ES (S&P 500 futures) | ~5 months (Dec 2025–May 2026) | Only 2025-2026 tariff volatility regime |
| NQ (Nasdaq futures) | ~5 months (Dec 2025–May 2026) | Same limitation as ES |

**Risk**: ES and NQ strategies were validated on a single, specific regime. The 2025 tariff shock environment may have unusually high volatility or directional bias that makes strategies look better than they are. A strategy tuned on Dec 2025–May 2026 data could fail badly in a range-bound or bear market environment.

**Current validation approach**: Walk-Forward Optimisation (WFO) + DSR (deflated Sharpe ratio). This is necessary but not sufficient for 5 months of data.

---

## 3. Data Expansion Targets

### Minimum Required
- 2+ years of ES and NQ tick or MBP-10 data
- Coverage must include materially different regimes (see below)

### Target Regime Coverage

| Period | Market Regime | Why Important |
|--------|--------------|---------------|
| Feb–Apr 2020 | COVID crash + recovery | Extreme volatility, gaps, limit moves |
| 2022 (full year) | Bear market, rate hike cycle | Trending down, elevated VIX |
| 2023 | Recovery / range | Post-bear stabilisation |
| 2024 | AI boom / momentum | Strong uptrend, low VIX |
| 2025 (H1) | Tariff volatility | High uncertainty, headline risk |

At minimum: include 2022 (bear) and 2020 (crash). These are the regimes most likely to break strategies optimised on calm/bullish periods.

### Data Management Rules

- Store raw tick/MBP-10 data in `01_data/raw/` — never modify raw files
- Derived bars (1m, 5m, 15m, etc.) live in `01_data/derived/`
- Every derived file must have a corresponding metadata JSON: `{symbol, bar_size, source, created_date, row_count, date_range}`
- Version derived feature files with a date suffix when methodology changes (e.g., `es_15m_features_v2_20260101.parquet`)
- Do not overwrite old derived files — append version suffix and keep both

---

## 4. Regime Slicing Plan

After expanding data, slice all strategy results across these regime dimensions. A strategy should show positive expectancy in at least half of these slices; strategies that only work in one regime are suspect.

### Market Condition Slices

| Slice Dimension | Low Bucket | High Bucket |
|----------------|-----------|------------|
| Volatility (VIX) | VIX < 18 | VIX > 25 |
| Trend vs Chop | ADX < 20 (chop) | ADX > 30 (trend) |
| Volume | Below 20-day avg | Above 20-day avg |
| Session | RTH only | Overnight only |
| News impact | Non-news days | FOMC/CPI/NFP days |
| Gold regime | GC in range | GC in uptrend |
| Equity regime | ES/NQ risk-on | ES/NQ risk-off |
| Spread | Normal spread | High spread (> 2x avg) |
| Calendar | Non-month-end | Month-end (last 3 days) |

### Regime Reporting Format

For each strategy, produce a regime table:

```
Strategy: vwap_reclaim_gc
Metric: Net Sharpe

Regime                  | Value | Trade Count | Verdict
------------------------|-------|-------------|--------
VIX < 18               | 1.8   | 45          | OK
VIX > 25               | 2.1   | 32          | OK
Trend (ADX>30)         | 2.4   | 28          | OK
Chop (ADX<20)          | 0.3   | 18          | WARN
RTH only               | 2.2   | 62          | OK
FOMC/CPI/NFP days      | -0.5  | 8           | FAIL — pause on news
```

Any strategy with a FAIL slice must have a documented plan: either exclude that regime from deployment or accept the risk explicitly.

---

## 5. Validation Upgrades

### Current Validation Stack
- Walk-Forward Optimisation (WFO) with IS/OOS splits
- Deflated Sharpe Ratio (DSR) accounting for multiple trials

### Required Additions

| Test | What It Checks | Priority |
|------|---------------|----------|
| PBO (Probability of Backtest Overfitting) | Combinatorial cross-validation estimate of overfit probability | High |
| CSCV (Combinatorially Symmetric Cross-Validation) | Structured IS/OOS splits across all parameter permutations | High |
| Family-level false discovery control | When testing N strategies, controls the rate of spurious survivors | High |
| White Reality Check / SPA test | Tests whether best strategy beats a benchmark by chance | Medium |
| Bootstrap-by-day analysis | Resample trade days with replacement; check Sharpe stability | Medium |
| Outlier-day removal sensitivity | Remove the top 5 best days; does the strategy still pass? | Medium |
| Walk-forward degradation report | Track IS Sharpe vs OOS Sharpe across all WFO windows | Medium |
| Train/test split by regime | Separate IS/OOS by regime type, not just date | High |

### Validation Thresholds (Post-Upgrade)

A strategy must pass **all** of the following to remain in the survivor pool:
- DSR > 0.5
- PBO < 0.25 (less than 25% probability of overfit)
- OOS Sharpe / IS Sharpe > 0.5 (degradation ratio)
- Survives outlier-day removal (top 5 days removed and strategy still net positive)
- Positive expectancy in at least 5 of 9 regime slices
- Minimum 30 OOS trades across all WFO windows

---

## 6. Slippage and Execution Stress Tests

Every strategy must be re-run under each of these execution scenarios. Results must be documented before demo deployment.

### Slippage Ladder

| Scenario | Additional Slippage | Acceptance Threshold |
|----------|--------------------|--------------------|
| Base (current) | 0.5 tick | Must pass |
| Light stress | 1 tick | Must pass |
| Moderate stress | 2 ticks | Should pass |
| Heavy stress | 3 ticks | Note degradation, document |

A strategy fails slippage stress if it becomes net-negative at the 1-tick scenario.

### Execution Scenario Matrix

| Scenario | Description | Test Method |
|----------|-------------|-------------|
| Missed fill | Entry order not filled, trade skipped | Zero out X% of entry fills |
| One-bar delay | Entry executes on bar N+1 open, not bar N close | Shift entry price by one bar |
| 250ms latency | Order arrives 250ms late | Use next tick >= 250ms after signal |
| 1s latency | Order arrives 1s late | Use next tick >= 1s after signal |
| 5s latency | Order arrives 5s late | Use next tick >= 5s after signal |
| Widened spread | Bid-ask spread doubled | Add 1 extra tick to each fill |
| Rejected order | Entry order rejected, trade missed | Randomly reject X% of orders |
| Bracket attach failure | Stop/target not attached, manual close | Force worst-case manual close |
| Partial data outage | Missing bars during a trade | Test with randomly dropped bars |
| Broker disconnect | Connection lost mid-trade | Test with forced position close at mid-bar |

All scenario results must be logged to a structured report. Strategy is only demo-eligible if it retains positive expectancy under scenarios up to 1s latency and 2-tick slippage.

---

## 7. Strategy Demo-Eligibility Rules

A strategy **cannot** proceed to demo trading unless it satisfies all of the following gates. Gates are non-negotiable.

### Gate Checklist

| Gate | Requirement |
|------|------------|
| Trade count | Minimum 100 OOS trades across WFO windows |
| Worst-day risk | Worst single-day loss < 50% of daily loss limit |
| Slippage survival | Net positive at 1-tick extra slippage |
| Regime slicing | Positive expectancy in >= 5 of 9 regime slices |
| Metrics complete | DSR, PBO, Sharpe, Calmar, max drawdown all computed |
| Data sparsity | No regime slice with < 10 trades — flag as untested |
| Signal path dry-run | Dry-run mode produces same signals as backtest logic |
| Allowlist | Strategy ID present in `config/allowlist.json` |
| Broker bracket support | Confirmed that broker supports native bracket orders for this instrument |
| ES/NQ minimum data | Not applicable to GC; ES/NQ strategies require 2+ year dataset before eligibility |

If any gate is missing or fails, the strategy is demoted to `RESEARCH` status and cannot be enabled.

---

## 8. Reports to Produce Later

These reports will be created once the data expansion and validation upgrades are complete. They are not produced now.

| Report File | Contents |
|-------------|----------|
| `evidence_upgrade_results.md` | Full results of re-validation after data expansion; which strategies survived, which were demoted |
| `live_vs_backtest_degradation.md` | Comparison of live demo metrics vs backtest expectations after 30+ demo days |
| `regime_stability_report.md` | Per-strategy regime slice tables; stability scores; strategies with regime-specific deployment rules |
| `strategy_demotion_report.md` | Record of any strategy demoted from survivor status; reason, date, evidence |
