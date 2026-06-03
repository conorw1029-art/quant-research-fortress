# Strategy Testing Factory — Design Document

**Version**: 1.0  
**Date**: 2026-06-03  
**Author**: Fortress Research  
**Status**: Active

---

## 1. Purpose

The Strategy Testing Factory is a systematic, anti-overfitting research pipeline designed to:

1. **Produce honest results** — every parameter combination tested is logged. No selective reporting. No cherry-picking the best combo post-hoc.
2. **Control the multiple testing problem** — with 256 parameter combinations per strategy and dozens of strategies per family, the probability of a spurious survivor is high. The factory applies family-level false discovery rate correction.
3. **Produce deployment-ready verdicts** — each strategy exits the pipeline with a machine-readable status and a human-readable deployment eligibility report.
4. **Maintain a research ledger** — all trials are appended to `zoo_research.jsonl`. This file is never overwritten. It is the audit trail.

The factory does not optimise. It validates. The parameter search is bounded and logged before testing begins, so the researcher cannot change the grid based on results.

---

## 2. The 13-Step Validation Pipeline

Each strategy that enters the factory runs through all 13 steps in sequence. A strategy may be rejected at any step — earlier failures skip downstream tests to conserve compute — but the rejection reason is always logged.

### Step 1 — Hypothesis Card Creation

Before any code runs, the researcher fills in a hypothesis card in `strategy_universe_exhaustive.json`:

```json
{
  "strategy_key": "CVD_VWAP",
  "family": "A_CVD",
  "priority": 1,
  "status": "BACKLOG",
  "hypothesis": "When cumulative volume delta is strongly positive AND price is above session VWAP, there is systematic order flow alignment that predicts continuation over the next 8 bars.",
  "signal_source": "L2 tick data (mbp-10)",
  "symbols": ["GC", "SI"],
  "param_grid": {
    "cvd_pct": [60, 70, 80],
    "band": [0.3, 0.5, 0.7],
    "hold_bars": [5, 8, 10]
  },
  "n_combos": 27,
  "expected_trades_per_year": 400,
  "rejection_threshold": "DSR < 0.3 or n_trades < 200 or 2-tick slippage kills P&L"
}
```

The hypothesis must be stated before results are known. This is the foundational anti-overfitting control.

### Step 2 — Parameter Grid Size Limiter

Maximum **256 parameter combinations** per strategy. If the declared param_grid produces more:

- The factory automatically sub-samples using a quasi-random (Sobol) sequence to preserve coverage.
- The researcher is warned and the original grid is logged alongside the sampled grid.
- No extra runs are permitted after seeing results.

The 256-combo limit is not arbitrary. With typical 5-year data and a 0.05 significance target, Bonferroni correction requires p < 0.05/256 = 0.000195 per test. This is achievable with n_trades >= 200 and genuine edge but is impossible to fake with random noise — which is the point.

**Enforcement**: `tick_strategy_research_factory.py --max-combos 256` (hard default). Override requires explicit `--max-combos N` with N logged.

### Step 3 — Walk-Forward Validation (2yr train / 1yr OOS rolling)

Rolling window validation prevents in-sample parameter selection from inflating out-of-sample metrics.

- **Train window**: 24 months
- **OOS window**: 12 months
- **Step size**: 6 months
- **Minimum folds**: 3 (strategies with less than 3 years of data are ineligible for WF validation)
- **Reported metric**: Mean WF Sharpe across all OOS folds

A strategy that looks good in-sample but degrades in WF is flagged `WF_FAILED`. This is the single most important test. A positive WF Sharpe does not guarantee live performance, but a negative WF Sharpe is a near-certain rejection.

**Pass criterion**: WF Sharpe >= 1.5 across all folds (no single fold below 0.0).

### Step 4 — Deflated Sharpe Ratio (Bailey 2014)

The Deflated Sharpe Ratio (DSR) adjusts the observed Sharpe for:

- Non-normality of returns (skewness and excess kurtosis)
- Multiple testing (the number of strategy variants and parameter combinations tried)

Formula (Bailey & Lopez de Prado, 2014):

```
SR_0 = E[max(SR_1, ..., SR_N)] under the null hypothesis
DSR = Phi( (SR_hat - SR_0) * sqrt(T-1) / sqrt(1 - skew*SR + (kurt+2)/4 * SR^2) )
```

Where Phi is the standard normal CDF, T is the number of daily returns, and N is the number of parameter combinations tested.

- **DSR > 0.95**: Strong evidence (p < 0.05 after multiple-testing correction) → strategy advances
- **DSR 0.80-0.95**: Weak evidence → strategy enters WATCHLIST
- **DSR < 0.80**: Strategy is rejected at this step

The DSR is computed on the best parameter combo's OOS returns — not the in-sample period.

### Step 5 — Probability of Backtest Overfitting (PBO/CSCV)

The CSCV (Combinatorially Symmetric Cross-Validation) method from Bailey et al. (2014) estimates the probability that a strategy's apparent edge is due to overfitting rather than genuine alpha.

Process:
1. Split the full backtest period into T equal-length sub-periods (T=8 or 16)
2. For all possible ways to partition into train/test halves, compute: does the best train-set combo rank in the top half on the test set?
3. PBO = fraction of partitions where the best training strategy performs below median in testing

**Pass criterion**: PBO < 0.40 (less than 40% probability of overfitting). Strategies with PBO >= 0.40 enter RESEARCH_ONLY status.

Implementation uses a lightweight approximation when the full CSCV is computationally infeasible (n_combos > 64): bootstrap the top-5 combos over 100 random train/test splits.

### Step 6 — Bootstrap-by-Day Null Hypothesis Test

Permutation test that does not assume any particular return distribution:

1. Compute the observed DSR/Sharpe on the original daily P&L series.
2. Shuffle the **dates** (not individual trades) 1,000 times.
3. Re-compute DSR/Sharpe on each shuffled series.
4. p-value = fraction of shuffled results >= observed result.

Bootstrap p-value controls for autocorrelation structure that parametric tests assume away.

**Pass criterion**: Bootstrap p-value < 0.05.

Note: Date shuffling preserves within-day trade structure (same trades, different calendar order), which tests whether the observed performance depends on calendar-time correlation (news, seasonality, trend) rather than the trading signal itself.

### Step 7 — Slippage Ladder (0, 1, 2, 3 ticks)

Real execution degrades with slippage. The slippage ladder answers: at what slippage level does the edge disappear?

| Ticks | GC cost/RT | SI cost/RT | Expected verdict |
|-------|-----------|-----------|-----------------|
| 0     | Theoretical | Theoretical | All strategies pass |
| 1     | $20 + $4.50 commission | $50 + $4.50 | Weak strategies fail |
| 2     | $40 + $4.50 | $100 + $4.50 | Marginal strategies fail |
| 3     | $60 + $4.50 | $150 + $4.50 | Only robust strategies survive |

**Pass criterion for deployment**: Strategy must be profitable at 2-tick slippage. 3-tick survival is the gold standard (marked `ROBUST` in the report).

Strategies that only survive at 0-tick (theoretical) are immediately rejected. Strategies that survive 1-tick but not 2-tick enter WATCHLIST. Only 2-tick+ survivors are eligible for paper/demo trading.

### Step 8 — News-Filtered Version Comparison

High-impact economic releases (FOMC, NFP, CPI, EIA, COT) cause price gaps and adversarial fills. Two versions are compared:

- **Full**: All bars included
- **News-filtered**: Bars within ±30 minutes of a scheduled release are excluded

If the filtered Sharpe is materially lower than full (> 25% degradation), the strategy's apparent edge may be driven by news reaction — not the stated thesis. Such strategies are flagged `NEWS_DEPENDENT`.

A NEWS_DEPENDENT strategy is not automatically rejected; it may be deployed with a news filter, but the expected trade count and P&L must be re-calculated on the filtered version.

### Step 9 — Regime Slicing (5 regimes)

Markets cycle through regimes. A robust strategy should work across multiple regimes, not just one.

The 5-year bar data is segmented into regimes based on 60-day rolling volatility and trend:

| Regime | Definition |
|--------|-----------|
| Bull | Close > 200-day MA and vol <= median vol |
| Bear | Close < 200-day MA and vol <= median vol |
| High-Vol | Rolling 20-day vol > 1.5x trailing median |
| Low-Vol | Rolling 20-day vol < 0.5x trailing median |
| Sideways | |Close - 200-day MA| < 1% and vol near median |

**Pass criterion**: Strategy must show positive Sharpe in at least 3 of 5 regimes. Strategies profitable in only 1 regime are flagged `REGIME_SENSITIVE` and require regime-filtering logic before deployment.

### Step 10 — Trade Count Minimum Checks

Statistical validity requires sufficient sample size. The minimum standards are:

| Validation level | Minimum trades | Notes |
|-----------------|---------------|-------|
| Basic screening | 50 | 5-year full period |
| Step 3 WF validation | 200 | In OOS periods combined |
| Full statistical suite (Steps 4-9) | 200 | Required for DSR/PBO/bootstrap |
| Deployment eligibility | 200 | Hard minimum; below this, no production approval |

Strategies with < 200 trades in 5 years are categorised as INSUFFICIENT_DATA and placed in WATCHLIST for re-evaluation if data window extends.

The 200-trade minimum is derived from: with n=200, the standard error of the Sharpe ratio is approximately 1/sqrt(200) = 0.071, giving 95% CI width of ±0.14 — tight enough to distinguish a Sharpe of 1.5 from noise at reasonable confidence.

### Step 11 — Portfolio Correlation Check

A strategy that passes all individual tests may still be rejected if it is too correlated with existing survivors. Adding a highly correlated strategy does not reduce portfolio risk; it concentrates it.

Process:
1. Reconstruct daily P&L series for the new strategy candidate.
2. Compute Pearson correlation against every existing confirmed survivor's daily P&L.
3. If any correlation > 0.70, flag the candidate as `HIGH_CORRELATION` and report the overlapping survivor.

**Pass criterion for addition**: All pairwise correlations with existing survivors < 0.70.

Exception: Two strategies from the same family (e.g., CVD_Microprice cvd=60 and cvd=70) may be allowed if their correlation is confirmed below 0.70 AND the marginal Sharpe improvement exceeds 0.3.

**Same-symbol conflict**: Two strategies that both trade GC in the same direction at the same time must be checked for net position doubling. If their signal overlap > 30%, only the higher-DSR variant advances.

### Step 12 — Family-Level False Discovery Control

When testing N strategies in the same family (e.g., testing 8 variants of CVD strategies), the probability of a false positive increases with N. The factory applies two corrections:

**Bonferroni Correction** (conservative):
- Adjusted significance level: alpha_adj = 0.05 / N_family_tests
- A strategy that passes with p = 0.03 but Bonferroni threshold is p < 0.005 does not pass

**Benjamini-Hochberg (BH) Correction** (less conservative, recommended when N > 20):
- Rank all p-values in the family from smallest to largest
- Strategy k passes if p_k <= (k/N) * 0.05
- Allows more discoveries while controlling the false discovery rate at 5%

Both corrections are computed and reported. The deployment recommendation uses BH correction. The researcher may apply Bonferroni if extra conservatism is warranted.

A family-level report is produced after all strategies in a family are tested, summarising the corrected significance of all trials.

### Step 13 — Deployment Eligibility Report

After all 12 preceding steps, a machine-readable report is generated:

```json
{
  "strategy_key": "CVD_VWAP",
  "symbol": "GC",
  "final_status": "DEMO_CANDIDATE",
  "best_params": {"cvd_pct": 60, "band": 0.5, "hold_bars": 8},
  "wf_sharpe": 3.6,
  "dsr": 0.97,
  "pbo": 0.18,
  "bootstrap_p": 0.003,
  "slippage_survival": "2-tick",
  "news_sensitive": false,
  "regime_pass_count": 4,
  "n_trades_5yr": 2800,
  "max_portfolio_correlation": 0.31,
  "fdr_pass_bonferroni": true,
  "fdr_pass_bh": true,
  "deployment_blocked_by": null,
  "recommended_action": "Add to demo trading. Monitor for 3 months. Target: 200 trades."
}
```

---

## 3. Status Lifecycle

Every strategy in the universe JSON has a status field. The lifecycle is:

```
BACKLOG
  │
  ├── [testing begins] ──────────────────────────► TESTING
  │
  └── [from TESTING, one of:]
        │
        ├── Steps 1-6 fail hard ──────────────────► REJECTED
        │     (no redemption; hypothesis was wrong)
        │
        ├── DSR 0.80-0.95 or PBO 0.35-0.40 ──────► WATCHLIST
        │     (promising but not statistically clear; re-test with more data)
        │
        ├── Passes Steps 1-9, fails Step 10 ───────► RESEARCH_ONLY
        │     (n_trades < 200; track but don't deploy)
        │
        ├── Passes Steps 1-10, fails Step 11 ──────► HIGH_CORRELATION
        │     (valid strategy; blocked by portfolio overlap; re-check if survivors change)
        │
        ├── Passes Steps 1-12, news dependent ──────► PAPER_CANDIDATE (with filter)
        │
        ├── Passes all 13 steps, 2-tick survivor ───► PAPER_CANDIDATE
        │
        ├── Paper trading confirms ──────────────────► DEMO_CANDIDATE
        │
        └── Demo confirms, all gates passed ─────────► LIVE_BLOCKED (until account slot opens)
```

Status transitions are written to the universe JSON with a timestamp and the reason for the transition. No status can be skipped (e.g., a strategy cannot go BACKLOG → DEMO_CANDIDATE without passing through PAPER_CANDIDATE).

Special status `RETEST`: applied when a previously REJECTED strategy has new evidence (extended data, corrected code bug) that warrants re-evaluation. The original test results are preserved; the retest is logged as a new entry in zoo_research.jsonl.

---

## 4. Anti-Cherry-Picking Controls

The factory enforces these controls mechanically, not by researcher discipline:

### 4.1 Pre-Registration

The parameter grid must be declared and locked in `strategy_universe_exhaustive.json` before the backtest runs. The factory records the hash of the grid file at the start of each run. If the grid changes between runs, the run is flagged as `MODIFIED_GRID` and all results from that run are marked with reduced confidence.

### 4.2 All-Trials Logging

Every parameter combination that is tested is written to `zoo_research.jsonl` regardless of result. The factory does not filter before logging. A researcher cannot delete bad results from the ledger without creating a detectable gap in the run_id sequence.

### 4.3 No Post-Hoc Grid Expansion

If a strategy passes screening, the researcher cannot add new parameter combos to "explore around the winner". The surviving parameters are fixed at whatever was declared in the original grid.

### 4.4 Sequential Testing Only

Results from one family are not visible during testing of another family. The factory processes families sequentially, with the family-level FDR report generated only after all strategies in the family are completed.

### 4.5 Independent Replication

The `--smoke-test` flag provides a reproducibility check. It runs the same strategy (CVD_VWAP on GC with default params) and checks that it produces > 0 trades and positive DSR. If the smoke test fails after a code change, all results since the last passing smoke test are flagged NEEDS_REVIEW.

---

## 5. Research Ledger (zoo_research.jsonl)

All trials are appended to `05_backtests/research_ledger/zoo_research.jsonl`. This file is the permanent research record.

### Format

One JSON object per line (JSONL):

```jsonl
{"timestamp": "2026-06-03T14:23:11Z", "strategy_key": "CVD_VWAP", "symbol": "GC", "params": {"cvd_pct": 60, "band": 0.5, "hold_bars": 8}, "n_trades": 2847, "wf_sharpe": 3.61, "dsr": 0.971, "bootstrap_p": 0.003, "slippage_1tick": 1.82, "slippage_2tick": 0.94, "slippage_3tick": -0.21, "status": "DEMO_CANDIDATE", "run_id": "run_20260603_001", "grid_hash": "a3f2c9d1"}
{"timestamp": "2026-06-03T14:24:05Z", "strategy_key": "CVD_VWAP", "symbol": "GC", "params": {"cvd_pct": 70, "band": 0.3, "hold_bars": 5}, "n_trades": 1203, "wf_sharpe": 1.2, "dsr": 0.44, "bootstrap_p": 0.18, "slippage_1tick": 0.61, "slippage_2tick": -0.3, "slippage_3tick": -1.1, "status": "REJECTED", "run_id": "run_20260603_001", "grid_hash": "a3f2c9d1"}
```

### Ledger Rules

1. Append-only. Never modify or delete existing lines.
2. Every run generates a unique `run_id` (timestamp + counter).
3. The `grid_hash` is the MD5 of the strategy's param_grid JSON. Any grid change produces a new hash, flagging the run.
4. The `status` field reflects the final verdict for that specific parameter combo — not the strategy family.
5. The file must remain valid JSONL (one complete JSON object per line) at all times.

### Querying the Ledger

```python
import pandas as pd

zoo = pd.read_json("05_backtests/research_ledger/zoo_research.jsonl", lines=True)
survivors = zoo[zoo["status"].isin(["PAPER_CANDIDATE", "DEMO_CANDIDATE"])]
print(survivors.groupby("strategy_key")["wf_sharpe"].max().sort_values(ascending=False))
```

---

## 6. Configuration Reference

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_combos` | 256 | Maximum parameter combinations per strategy |
| `wf_train_months` | 24 | Walk-forward training window in months |
| `wf_oos_months` | 12 | Walk-forward OOS window in months |
| `wf_step_months` | 6 | Walk-forward step size in months |
| `dsr_pass_threshold` | 0.95 | DSR probability required to pass Step 4 |
| `dsr_watchlist_threshold` | 0.80 | DSR probability for WATCHLIST |
| `pbo_pass_threshold` | 0.40 | Maximum PBO to pass Step 5 |
| `bootstrap_n_shuffles` | 1000 | Number of date-shuffles for bootstrap p-value |
| `bootstrap_p_threshold` | 0.05 | Maximum bootstrap p-value to pass Step 6 |
| `slippage_deployment_ticks` | 2 | Minimum slippage tolerance for deployment |
| `min_trades_full_validation` | 200 | Minimum OOS trades for full statistical suite |
| `max_portfolio_correlation` | 0.70 | Maximum correlation with existing survivors |
| `fdr_method` | "bh" | False discovery rate method: "bonferroni" or "bh" |
| `fdr_alpha` | 0.05 | Family-wise error rate target |

---

## 7. File Layout

```
05_backtests/
  research_ledger/
    zoo_research.jsonl          ← append-only trial log
  l2_results/
    GC_passed_evidence.json     ← GC evidence-grade survivors
    SI_passed_evidence.json     ← SI evidence-grade survivors

08_docs/
  strategy_universe_exhaustive.json  ← pre-registered strategy catalog
  portfolio_candidate_report.json    ← portfolio optimizer output
  strategy_testing_factory_design.md ← this document
  portfolio_construction_master_plan.md
```
