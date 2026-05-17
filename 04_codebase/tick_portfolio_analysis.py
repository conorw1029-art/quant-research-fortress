#!/usr/bin/env python3
"""
L2 Tick Portfolio Analysis
===========================
Reads all tick_results_*.json files, aggregates survivors across bar sizes,
runs portfolio-level analysis, and compares against existing OHLCV portfolio.

Sections:
  1. Cross-bar-size survivor ranking
  2. Correlation matrix (are survivors truly uncorrelated?)
  3. Portfolio equity curve (equal-weight unit sizing)
  4. Monte Carlo (10,000 paths) on combined portfolio
  5. Topstep safety check (daily loss limit, trailing DD)
  6. OHLCV + L2 combined portfolio analysis
  7. Final summary report saved to JSON

Usage:
  python tick_portfolio_analysis.py
"""

import json
import sys
import warnings
from pathlib import Path
from datetime import datetime
from collections import Counter

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULT_DIR  = Path(__file__).parent.parent / "05_backtests"
BAR_DIR     = Path(__file__).parent.parent / "01_data" / "tick_bars"
OHLCV_PNL   = RESULT_DIR / "daily_portfolio_pnl.csv"

# Add codebase to path so tick_backtest_engine and tick_strategies can be found
sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import run_backtest, SPECS  # noqa: E402
from tick_strategies import STRATEGY_MAP              # noqa: E402

DSR_MIN     = 1.0
TOPSTEP_DAILY_LIMIT   = 4_500.0
TOPSTEP_TRAILING_DD   = 7_500.0
MC_SIMS               = 10_000


# ── Load all result files ────────────────────────────────────────────────────

def load_all_results() -> pd.DataFrame:
    files = sorted(RESULT_DIR.glob("tick_results_*.json"))
    if not files:
        print("No tick_results_*.json files found in", RESULT_DIR)
        sys.exit(1)

    all_rows = []
    for f in files:
        with open(f) as fh:
            rows = json.load(fh)
        for r in rows:
            r["source_file"] = f.name
        all_rows.extend(rows)

    df = pd.DataFrame(all_rows)
    print(f"Loaded {len(df)} results from {len(files)} files")
    return df


# ── DSR-ranked survivor table ────────────────────────────────────────────────

def survivor_table(df: pd.DataFrame) -> pd.DataFrame:
    survivors = df[df["grade"].isin(["EXCELLENT", "GOOD", "MARGINAL"])].copy()
    survivors = survivors.sort_values("dsr", ascending=False)
    # Keep fold_results so reconstruct_daily_pnl can extract best params
    display_cols = ["symbol", "strategy", "bar_minutes", "dsr", "sharpe", "win_rate",
                    "n_oos_trades", "total_pnl", "max_dd", "calmar", "grade",
                    "stress_pct_profitable", "sharpe_vs_random"]
    all_cols = display_cols + ["fold_results"]
    cols = [c for c in all_cols if c in survivors.columns]
    return survivors[cols].reset_index(drop=True)


# ── Rebuild OOS daily P&L for a strategy ────────────────────────────────────

def reconstruct_daily_pnl(result_row: dict) -> pd.Series | None:
    """
    Re-run the best-found strategy params on full data to get daily P&L.
    Uses the most-common best params across WFO folds.
    """
    symbol     = result_row["symbol"]
    strat_name = result_row["strategy"]
    bar_min    = int(result_row["bar_minutes"])

    bar_path = BAR_DIR / f"{symbol}_bars_{bar_min}m.parquet"
    if not bar_path.exists():
        return None

    strat = STRATEGY_MAP.get(strat_name)
    if strat is None:
        return None

    fold_results = result_row.get("fold_results", [])
    # fold_results might be NaN (pandas fills missing cols) — treat as empty
    try:
        if fold_results is None or (isinstance(fold_results, float) and np.isnan(fold_results)):
            fold_results = []
    except (TypeError, ValueError):
        pass
    if not fold_results or not isinstance(fold_results, list):
        return None

    param_keys = list(strat["param_grid"].keys())
    best_params = {}
    for k in param_keys:
        vals = [f["best_params"].get(k)
                for f in fold_results
                if isinstance(f, dict) and "best_params" in f and k in f["best_params"]]
        if vals:
            best_params[k] = Counter(vals).most_common(1)[0][0]

    if not best_params and isinstance(fold_results[-1], dict):
        best_params = fold_results[-1].get("best_params", {})

    if not best_params:
        return None

    try:
        df = pd.read_parquet(bar_path)
        df.index = pd.to_datetime(df.index, utc=True)
        sig    = strat["compute"](df, **best_params)
        trades = run_backtest(df, sig, symbol,
                              stop_atr_mult=1.5, tp_atr_mult=3.0, max_hold_bars=50)
    except Exception:
        return None

    if trades.empty:
        return None

    trades["date"] = pd.to_datetime(trades["entry_time"]).dt.date
    daily = trades.groupby("date")["dollar_pnl"].sum()
    daily.index = pd.to_datetime(daily.index)
    return daily


# ── Correlation matrix ───────────────────────────────────────────────────────

def build_correlation_matrix(survivors: pd.DataFrame) -> pd.DataFrame:
    print("\nBuilding OOS daily P&L series for correlation analysis...")
    series_dict = {}
    for _, row in survivors.head(30).iterrows():  # top 30
        key = f"{row['symbol']}_{row['strategy']}_{row['bar_minutes']}m"
        s = reconstruct_daily_pnl(row.to_dict())
        if s is not None and len(s) > 30:
            series_dict[key] = s
            print(f"  {key}: {len(s)} days")

    if len(series_dict) < 2:
        print("  Not enough series for correlation analysis")
        return pd.DataFrame()

    combined = pd.DataFrame(series_dict).fillna(0)
    corr = combined.corr()
    return corr


# ── Monte Carlo on combined portfolio ────────────────────────────────────────

def monte_carlo_portfolio(daily_pnl: np.ndarray, n_sims: int = MC_SIMS) -> dict:
    rng    = np.random.default_rng(42)
    n_days = len(daily_pnl)
    if n_days < 20:
        return {}

    max_trail_dds  = np.zeros(n_sims)
    worst_days     = np.zeros(n_sims)
    final_pnls     = np.zeros(n_sims)
    daily_viol     = np.zeros(n_sims, dtype=bool)

    for i in range(n_sims):
        sim    = rng.choice(daily_pnl, size=n_days, replace=True)
        equity = np.cumsum(sim)
        peak   = np.maximum.accumulate(equity)
        dd     = peak - equity
        max_trail_dds[i] = dd.max()
        worst_days[i]    = sim.min()
        final_pnls[i]    = sim.sum()
        daily_viol[i]    = (sim < -TOPSTEP_DAILY_LIMIT).any()

    return {
        "p_daily_breach":      float(daily_viol.mean()),
        "p_trailing_dd_breach": float((max_trail_dds > TOPSTEP_TRAILING_DD).mean()),
        "worst_day_p50":       float(np.percentile(worst_days, 50)),
        "worst_day_p99":       float(np.percentile(worst_days, 99)),
        "max_trail_dd_p50":    float(np.percentile(max_trail_dds, 50)),
        "max_trail_dd_p90":    float(np.percentile(max_trail_dds, 90)),
        "max_trail_dd_p99":    float(np.percentile(max_trail_dds, 99)),
        "final_pnl_p5":        float(np.percentile(final_pnls, 5)),
        "final_pnl_p50":       float(np.percentile(final_pnls, 50)),
        "final_pnl_p95":       float(np.percentile(final_pnls, 95)),
    }


# ── Combined OHLCV + L2 portfolio ────────────────────────────────────────────

def combined_portfolio_analysis(l2_daily: pd.Series) -> dict:
    if not OHLCV_PNL.exists():
        print(f"  OHLCV daily P&L not found at {OHLCV_PNL} — skipping combined analysis")
        return {}

    ohlcv = pd.read_csv(OHLCV_PNL, index_col=0, parse_dates=True)
    if "portfolio_pnl" not in ohlcv.columns:
        ohlcv["portfolio_pnl"] = ohlcv.sum(axis=1)

    ohlcv_daily = ohlcv["portfolio_pnl"]
    ohlcv_daily.index = pd.to_datetime(ohlcv_daily.index)

    # Align on common dates
    combined = pd.DataFrame({
        "ohlcv": ohlcv_daily,
        "l2":    l2_daily,
    }).fillna(0)

    combined["total"] = combined["ohlcv"] + combined["l2"]

    pnl = combined["total"].values
    mc  = monte_carlo_portfolio(pnl)

    corr = combined["ohlcv"].corr(combined["l2"])

    return {
        "ohlcv_l2_correlation":    float(corr),
        "combined_monthly_wr":     float((combined["total"].resample("ME").sum() > 0).mean()),
        "combined_avg_monthly_pnl": float(combined["total"].resample("ME").sum().mean()),
        **{f"combined_{k}": v for k, v in mc.items()},
    }


# ── Print full report ────────────────────────────────────────────────────────

def print_report(survivors: pd.DataFrame, mc_l2: dict, mc_combined: dict,
                 corr: pd.DataFrame) -> None:
    print(f"\n{'='*90}")
    print(f"  L2 PORTFOLIO ANALYSIS — FULL REPORT")
    print(f"{'='*90}")
    print(f"\n  TOP SURVIVORS BY DSR:")
    print(f"  {'#':<3} {'Symbol':<6} {'Strategy':<32} {'Bar':>4} {'DSR':>6} {'Sharpe':>7} {'WR%':>6} {'TotPnL':>10} {'Grade'}")
    print(f"  {'-'*80}")
    for i, row in survivors.head(20).iterrows():
        print(f"  {i+1:<3} {row['symbol']:<6} {row['strategy']:<32} "
              f"{row.get('bar_minutes','-'):>4} {row.get('dsr',0):>6.2f} "
              f"{row.get('sharpe',0):>7.2f} {row.get('win_rate',0)*100:>5.1f}% "
              f"{row.get('total_pnl',0):>10,.0f}  {row.get('grade','')}")

    if mc_l2:
        print(f"\n  L2-ONLY PORTFOLIO MONTE CARLO (10k paths):")
        print(f"    P(daily limit breach):    {mc_l2.get('p_daily_breach', 0)*100:.2f}%")
        print(f"    P(trailing DD > $7,500):  {mc_l2.get('p_trailing_dd_breach', 0)*100:.2f}%")
        print(f"    Max trailing DD p50/p90:  ${mc_l2.get('max_trail_dd_p50',0):,.0f}  /  ${mc_l2.get('max_trail_dd_p90',0):,.0f}")
        print(f"    Final P&L  p5/p50/p95:   ${mc_l2.get('final_pnl_p5',0):,.0f}  /  ${mc_l2.get('final_pnl_p50',0):,.0f}  /  ${mc_l2.get('final_pnl_p95',0):,.0f}")

    if mc_combined:
        print(f"\n  COMBINED OHLCV + L2 PORTFOLIO MONTE CARLO (10k paths):")
        print(f"    OHLCV / L2 correlation:   {mc_combined.get('ohlcv_l2_correlation', 0):.3f}")
        print(f"    Monthly win rate:          {mc_combined.get('combined_monthly_wr', 0)*100:.1f}%")
        print(f"    Avg monthly P&L:          ${mc_combined.get('combined_avg_monthly_pnl', 0):,.0f}")
        print(f"    P(daily limit breach):    {mc_combined.get('combined_p_daily_breach', 0)*100:.2f}%")
        print(f"    P(trailing DD > $7,500):  {mc_combined.get('combined_p_trailing_dd_breach', 0)*100:.2f}%")
        print(f"    Max trail DD p50/p90:     ${mc_combined.get('combined_max_trail_dd_p50',0):,.0f}  /  ${mc_combined.get('combined_max_trail_dd_p90',0):,.0f}")

    if not corr.empty:
        print(f"\n  INTER-STRATEGY CORRELATIONS (top L2 survivors):")
        avg_corr = corr.values[np.triu_indices_from(corr.values, k=1)].mean()
        max_corr = corr.values[np.triu_indices_from(corr.values, k=1)].max()
        print(f"    Average pairwise correlation: {avg_corr:.3f}")
        print(f"    Max pairwise correlation:     {max_corr:.3f}")
        highly_corr = [(corr.index[i], corr.columns[j], corr.iloc[i, j])
                       for i in range(len(corr)) for j in range(i+1, len(corr))
                       if abs(corr.iloc[i, j]) > 0.5]
        if highly_corr:
            print(f"    Pairs with |corr| > 0.5:")
            for a, b, c in sorted(highly_corr, key=lambda x: -abs(x[2]))[:10]:
                print(f"      {a}  vs  {b}  :  {c:.3f}")
        else:
            print(f"    No pairs with |corr| > 0.5 — all strategies are independent")

    print(f"\n{'='*90}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"  L2 TICK PORTFOLIO ANALYSIS")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    # 1. Load all results
    all_results = load_all_results()

    # 2. Survivor table
    survivors = survivor_table(all_results)
    print(f"\n  Total survivors (DSR >= {DSR_MIN}): {len(survivors)}")
    print(f"  By grade:")
    for g in ["EXCELLENT", "GOOD", "MARGINAL"]:
        n = (survivors["grade"] == g).sum()
        print(f"    {g:<10}: {n}")

    # 3. Per-strategy unique count (dedupe by symbol+strategy across bar sizes)
    unique = survivors.drop_duplicates(subset=["symbol", "strategy"])
    print(f"  Unique (symbol, strategy) pairs: {len(unique)}")

    # 4. Correlation matrix
    corr = build_correlation_matrix(survivors)

    # 5. Build L2 portfolio daily P&L
    print("\nBuilding L2 portfolio equity curve...")
    l2_daily_list = []
    for _, row in survivors.head(20).iterrows():  # top 20 by DSR
        s = reconstruct_daily_pnl(row.to_dict())
        if s is not None:
            l2_daily_list.append(s)

    if l2_daily_list:
        l2_portfolio = pd.concat(l2_daily_list, axis=1).fillna(0).sum(axis=1)
        l2_portfolio.index = pd.to_datetime(l2_portfolio.index)

        print(f"  L2 portfolio: {len(l2_portfolio)} days, "
              f"total P&L ${l2_portfolio.sum():,.0f}")

        # Monthly stats
        monthly = l2_portfolio.resample("ME").sum()
        monthly_wr = (monthly > 0).mean()
        print(f"  Monthly win rate: {monthly_wr*100:.1f}%")
        print(f"  Avg monthly P&L:  ${monthly.mean():,.0f}")

        # 6. Monte Carlo — L2 only
        mc_l2 = monte_carlo_portfolio(l2_portfolio.values)

        # 7. Combined OHLCV + L2
        mc_combined = combined_portfolio_analysis(l2_portfolio)
    else:
        print("  No L2 daily P&L series reconstructed — skipping MC")
        l2_portfolio = pd.Series(dtype=float)
        mc_l2 = {}
        mc_combined = {}

    # 8. Print full report
    print_report(survivors, mc_l2, mc_combined, corr)

    # 9. Save summary
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = RESULT_DIR / f"tick_portfolio_analysis_{ts}.json"

    surv_csv_cols = [c for c in survivors.columns if c != "fold_results"]
    summary = {
        "timestamp":    ts,
        "n_results":    len(all_results),
        "n_survivors":  len(survivors),
        "survivors":    survivors[surv_csv_cols].head(30).to_dict(orient="records"),
        "mc_l2":        mc_l2,
        "mc_combined":  mc_combined,
        "corr_avg":     float(corr.values[np.triu_indices_from(corr.values, k=1)].mean())
                        if not corr.empty else None,
    }

    def _serial(obj):
        if isinstance(obj, (np.integer,)):   return int(obj)
        if isinstance(obj, (np.floating,)):  return float(obj)
        if isinstance(obj, (np.ndarray,)):   return obj.tolist()
        if pd.isna(obj):                     return None
        raise TypeError(type(obj))

    with open(out, "w") as f:
        json.dump(summary, f, indent=2, default=_serial)

    print(f"\n  Portfolio analysis saved: {out}")
    csv_cols = [c for c in survivors.columns if c != "fold_results"]
    survivors[csv_cols].to_csv(RESULT_DIR / f"tick_survivors_{ts}.csv", index=False)
    print(f"  Survivors CSV saved: {RESULT_DIR / f'tick_survivors_{ts}.csv'}")


if __name__ == "__main__":
    main()
