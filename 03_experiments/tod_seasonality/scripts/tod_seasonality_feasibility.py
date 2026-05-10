#!/usr/bin/env python3
"""
H8: Time-of-Day Seasonality — Feasibility Study
==================================================
Thesis: Specific 30-min RTH windows have persistent directional bias
        due to institutional order flow (open imbalances, MOC orders).

Method:
  Phase 1 (IS): Scan all 13 half-hour windows. Rank by |t-stat|.
  Phase 2 (OOS): Validate top-3 IS windows. Bonferroni alpha = 0.0167.

Signal: Long/short at window open, exit at window close.
        Direction = sign of IS mean return for that window.
Cost: MES 0.52 pts/RT.
Split: IS 2010-2018, OOS 2019-2024.
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


# ── Configuration ──────────────────────────────────────────────────
COST_MES = 0.52
COST_ES  = 1.20

IS_END    = "2018-12-31"
OOS_START = "2019-01-01"

# 13 half-hour windows in RTH
WINDOWS = []
for h in range(9, 16):
    for m in [0, 30]:
        start = dt.time(h, m)
        end_h, end_m = (h, m + 30) if m == 0 else (h + 1, 0)
        if end_h > 16 or (end_h == 16 and end_m > 0):
            continue
        if start < dt.time(9, 30):
            continue
        end = dt.time(end_h, end_m)
        WINDOWS.append((start, end))

TOP_N = 3  # Number of IS windows to validate OOS
BONFERRONI_ALPHA = 0.05 / TOP_N

CRITERIA = {
    "p_value":         BONFERRONI_ALPHA,
    "profit_factor":   1.25,
    "win_rate":        0.50,
    "both_halves_pos": True,
}


# ── Data Loading ───────────────────────────────────────────────────
def load_data(path: str, source_tz: str, col_ts: str) -> pd.DataFrame:
    """Load 1-min bars, filter RTH, keep 1-min resolution."""
    print(f"Loading {path} ...")
    df = pd.read_csv(
        path,
        usecols=[col_ts, "open", "high", "low", "close", "volume"],
        parse_dates=[col_ts],
    )
    df = df.rename(columns={col_ts: "timestamp"})
    df = df.sort_values("timestamp").reset_index(drop=True)

    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(source_tz)
    df["timestamp"] = df["timestamp"].dt.tz_convert("US/Eastern")

    # Filter RTH: 09:30 - 16:00 ET
    t = df["timestamp"].dt.time
    rth = (t >= dt.time(9, 30)) & (t < dt.time(16, 0))
    df = df.loc[rth].copy()
    df["date"] = df["timestamp"].dt.date

    print(f"  {len(df):,} 1-min RTH bars  {df['date'].iloc[0]} -> {df['date'].iloc[-1]}")
    print(f"  {df['date'].nunique()} trading days")
    return df


# ── Window Return Calculation ──────────────────────────────────────
def compute_window_returns(df: pd.DataFrame, window_start: dt.time,
                           window_end: dt.time) -> pd.DataFrame:
    """
    For each trading day, compute the return from window_start to window_end.
    Uses the open of the first bar at/after window_start and close of the
    last bar before window_end.
    """
    t = df["timestamp"].dt.time
    mask = (t >= window_start) & (t < window_end)
    window_bars = df.loc[mask].copy()

    if len(window_bars) == 0:
        return pd.DataFrame()

    # Group by date, get open of first bar and close of last bar
    daily = window_bars.groupby("date").agg(
        open_price=("open", "first"),
        close_price=("close", "last"),
        n_bars=("close", "count"),
    )

    # Filter days with incomplete windows (< 25 bars for 30-min window)
    min_bars = 20  # allow some tolerance for missing bars
    daily = daily[daily["n_bars"] >= min_bars].copy()

    daily["return_pts"] = daily["close_price"] - daily["open_price"]
    daily["date"] = daily.index

    return daily


# ── Evaluation ─────────────────────────────────────────────────────
def evaluate_window(returns_pts: np.ndarray, direction: int, cost: float,
                    label: str, alpha: float, verbose: bool = True) -> dict:
    """
    Evaluate a directional window trade.
    direction: +1 (long) or -1 (short). Applied to raw returns.
    """
    n = len(returns_pts)
    if n == 0:
        if verbose:
            print(f"  [{label}] No trades.")
        return {"pass": False, "n": 0}

    # Apply direction and cost
    pnl = returns_pts * direction - cost
    mean_pnl = np.mean(pnl)
    median_pnl = np.median(pnl)

    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    wr = len(wins) / n
    gross_profit = np.sum(wins) if len(wins) > 0 else 0
    gross_loss = np.abs(np.sum(losses)) if len(losses) > 0 else 1e-9
    pf = gross_profit / gross_loss
    avg_loss = np.mean(np.abs(losses)) if len(losses) > 0 else 1e-9

    # Equity curve and drawdown
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(equity)
    max_dd = np.min(equity - peak)

    # One-sided t-test
    if np.std(pnl) > 0:
        t_stat, p_two = stats.ttest_1samp(pnl, 0)
        p_value = p_two / 2 if t_stat > 0 else 1.0 - p_two / 2
    else:
        t_stat, p_value = 0, 1.0

    # Both-halves check
    half = n // 2
    h1_mean = np.mean(pnl[:half])
    h2_mean = np.mean(pnl[half:])
    both_pos = h1_mean > 0 and h2_mean > 0

    # Sharpe (annualized, ~252 trades/year)
    trades_per_year = 252
    sharpe_per_trade = np.mean(pnl) / np.std(pnl) if np.std(pnl) > 0 else 0
    sharpe_ann = sharpe_per_trade * np.sqrt(trades_per_year)
    h1_sharpe = (np.mean(pnl[:half]) / np.std(pnl[:half]) * np.sqrt(trades_per_year)
                 if np.std(pnl[:half]) > 0 else 0)
    h2_sharpe = (np.mean(pnl[half:]) / np.std(pnl[half:]) * np.sqrt(trades_per_year)
                 if np.std(pnl[half:]) > 0 else 0)

    # Check criteria
    failures = []
    if mean_pnl <= 0:
        failures.append("mean_pnl_positive")
    if p_value >= alpha:
        failures.append("p_value")
    if pf < CRITERIA["profit_factor"]:
        failures.append("profit_factor")
    if wr < CRITERIA["win_rate"]:
        failures.append("win_rate")
    if not both_pos:
        failures.append("both_halves_pos")

    passed = len(failures) == 0
    tag = "PASS" if passed else "FAIL"

    if verbose:
        dir_str = "LONG" if direction == 1 else "SHORT"
        print(f"\n  [{tag}] {label} ({dir_str})")
        print(f"    n={n:4d}  mean={mean_pnl:+.4f}pts  median={median_pnl:+.4f}pts"
              f"  WR={wr*100:.1f}%  PF={pf:.3f}  p={p_value:.5f}")
        print(f"    MaxDD={abs(max_dd):.1f}pts  AvgLoss={avg_loss:.4f}pts"
              f"  Sharpe={sharpe_ann:.3f}  Sharpe[H1/H2]={h1_sharpe:.3f}/{h2_sharpe:.3f}")
        if failures:
            print(f"    FAILED: {', '.join(failures)}")

    return {
        "pass": passed, "n": n, "mean": mean_pnl, "median": median_pnl,
        "wr": wr, "pf": pf, "p_value": p_value, "max_dd": max_dd,
        "sharpe": sharpe_ann, "h1_sharpe": h1_sharpe, "h2_sharpe": h2_sharpe,
        "t_stat": t_stat, "direction": direction, "failures": failures,
    }


def print_annual(dates, pnl_array, direction, cost):
    """Print annual breakdown."""
    df_tmp = pd.DataFrame({"date": dates, "pnl": pnl_array * direction - cost})
    df_tmp["year"] = pd.to_datetime(df_tmp["date"]).dt.year
    print("    Annual:")
    for year, grp in df_tmp.groupby("year"):
        print(f"      {year}: n={len(grp):3d}  mean={grp['pnl'].mean():+.4f}pts"
              f"  total={grp['pnl'].sum():+.1f}pts")


# ── Main ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="H8: Time-of-Day Seasonality")
    parser.add_argument("--input", required=True, help="Path to ES 1-min CSV")
    parser.add_argument("--source-tz", default="utc")
    parser.add_argument("--col-timestamp", default="ts_event")
    args = parser.parse_args()

    df = load_data(args.input, args.source_tz, args.col_timestamp)

    print(f"\n{'='*70}")
    print(f"  TIME-OF-DAY SEASONALITY -- HYPOTHESIS 8")
    print(f"{'='*70}")
    print(f"  Scan all 13 half-hour RTH windows for directional bias.")
    print(f"  Phase 1: IS discovery. Phase 2: OOS validation of top {TOP_N}.")
    print(f"  Cost: MES {COST_MES}pts/RT")
    print(f"  Windows: {len(WINDOWS)}")

    # ── Phase 1: IS Discovery ──────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  PHASE 1: IN-SAMPLE DISCOVERY (2010 -- 2018)")
    print(f"{'='*70}")

    # Split data
    is_df = df[pd.to_datetime(df["date"]) <= pd.Timestamp(IS_END)]
    oos_df = df[pd.to_datetime(df["date"]) >= pd.Timestamp(OOS_START)]

    print(f"  IS bars: {len(is_df):,}  |  OOS bars: {len(oos_df):,}")

    # Scan all windows
    print(f"\n  {'Window':<16s} {'n':>5s} {'mean_ret':>10s} {'std':>8s}"
          f" {'t_stat':>8s} {'p_val':>8s} {'direction':>10s}")
    print(f"  {'-'*72}")

    window_stats = []
    for w_start, w_end in WINDOWS:
        daily = compute_window_returns(is_df, w_start, w_end)
        if len(daily) == 0:
            continue

        rets = daily["return_pts"].values
        n = len(rets)
        mean_r = np.mean(rets)
        std_r = np.std(rets, ddof=1)
        t_stat, p_val = stats.ttest_1samp(rets, 0)

        # Direction = sign of IS mean
        direction = 1 if mean_r >= 0 else -1
        dir_str = "LONG" if direction == 1 else "SHORT"

        label = f"{w_start.strftime('%H:%M')}-{w_end.strftime('%H:%M')}"
        print(f"  {label:<16s} {n:5d} {mean_r:+10.4f} {std_r:8.4f}"
              f" {t_stat:+8.3f} {p_val:8.5f} {dir_str:>10s}")

        window_stats.append({
            "start": w_start, "end": w_end, "label": label,
            "n": n, "mean": mean_r, "std": std_r,
            "t_stat": t_stat, "p_val": p_val,
            "direction": direction,
        })

    # Rank by absolute t-stat
    window_stats.sort(key=lambda x: abs(x["t_stat"]), reverse=True)

    print(f"\n  Top {TOP_N} windows by |t-stat|:")
    for i, ws in enumerate(window_stats[:TOP_N]):
        dir_str = "LONG" if ws["direction"] == 1 else "SHORT"
        print(f"    {i+1}. {ws['label']}  t={ws['t_stat']:+.3f}  p={ws['p_val']:.5f}"
              f"  mean={ws['mean']:+.4f}pts  dir={dir_str}")

    # ── Phase 2: OOS Validation ────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  PHASE 2: OUT-OF-SAMPLE VALIDATION (2019 -- 2024)")
    print(f"  Bonferroni alpha: {BONFERRONI_ALPHA:.4f}")
    print(f"{'='*70}")

    survivors = []
    for i, ws in enumerate(window_stats[:TOP_N]):
        daily_oos = compute_window_returns(oos_df, ws["start"], ws["end"])
        if len(daily_oos) == 0:
            print(f"\n  [{ws['label']}] No OOS data.")
            continue

        rets_oos = daily_oos["return_pts"].values
        dates_oos = daily_oos["date"].values

        result = evaluate_window(
            rets_oos, ws["direction"], COST_MES,
            f"OOS | {ws['label']}", BONFERRONI_ALPHA, verbose=True,
        )
        print_annual(dates_oos, rets_oos, ws["direction"], COST_MES)

        if result["pass"]:
            survivors.append((ws, result))

    # ── Also show IS results for top windows (for completeness) ──
    print(f"\n{'='*70}")
    print(f"  IS DETAILED RESULTS (top {TOP_N} windows)")
    print(f"{'='*70}")

    for i, ws in enumerate(window_stats[:TOP_N]):
        daily_is = compute_window_returns(is_df, ws["start"], ws["end"])
        if len(daily_is) == 0:
            continue

        rets_is = daily_is["return_pts"].values
        dates_is = daily_is["date"].values

        result = evaluate_window(
            rets_is, ws["direction"], COST_MES,
            f"IS  | {ws['label']}", BONFERRONI_ALPHA, verbose=True,
        )
        print_annual(dates_is, rets_is, ws["direction"], COST_MES)

    # ── Verdict ─────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  VERDICT")
    print(f"{'='*70}")

    if survivors:
        print(f"\n  SIGNAL DETECTED -- {len(survivors)} window(s) passed OOS:")
        for ws, result in survivors:
            dir_str = "LONG" if ws["direction"] == 1 else "SHORT"
            print(f"    -> {ws['label']} ({dir_str})")
            print(f"       mean={result['mean']:+.4f}pts  WR={result['wr']*100:.1f}%"
                  f"  PF={result['pf']:.3f}  p={result['p_value']:.5f}"
                  f"  Sharpe={result['sharpe']:.3f}")
    else:
        print(f"\n  NO SIGNAL persists out-of-sample.")
        print(f"  REJECT Hypothesis 8.")

    # ── ES cost reference ───────────────────────────────────────
    if window_stats:
        print(f"\n{'='*70}")
        print(f"  REFERENCE: ES cost (best OOS window)")
        print(f"{'='*70}")

        best_ws = window_stats[0]
        daily_oos = compute_window_returns(oos_df, best_ws["start"], best_ws["end"])
        if len(daily_oos) > 0:
            rets_oos = daily_oos["return_pts"].values
            dates_oos = daily_oos["date"].values
            evaluate_window(
                rets_oos, best_ws["direction"], COST_ES,
                f"OOS | {best_ws['label']} (ES cost)", BONFERRONI_ALPHA,
            )
            print_annual(dates_oos, rets_oos, best_ws["direction"], COST_ES)

    print(f"\n{'='*70}")
    print(f"  COMPLETE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()