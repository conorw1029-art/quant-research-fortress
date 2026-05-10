#!/usr/bin/env python3
"""
H7: Turn-of-Month Effect — Feasibility Study
==============================================
Academic basis: Ariel (1987), Lakonishok & Smidt (1988)
Thesis: Persistent bullish bias around month boundaries due to
        pension inflows, payroll buying, window dressing.

Signal: Long from T-N (N trading days before month-end) through
        T+M (Mth trading day of new month).

Pre-registered: 4 variants, Bonferroni alpha = 0.0125
Data: ES 1-min bars resampled to daily RTH close.
Cost: MES 0.52 pts/RT.
Split: IS 2010-2018, OOS 2019-2024.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


# ── Configuration ──────────────────────────────────────────────────
COST_MES = 0.52  # pts round-trip
COST_ES  = 1.20

IS_END   = "2018-12-31"
OOS_START = "2019-01-01"

VARIANTS = [
    {"name": "T-2 -> T+3", "entry_before": 2, "exit_after": 3},
    {"name": "T-1 -> T+3", "entry_before": 1, "exit_after": 3},
    {"name": "T-2 -> T+2", "entry_before": 2, "exit_after": 2},
    {"name": "T-1 -> T+2", "entry_before": 1, "exit_after": 2},
]

BONFERRONI_ALPHA = 0.05 / len(VARIANTS)

# Passing criteria (same as all prior hypotheses)
CRITERIA = {
    "p_value":          BONFERRONI_ALPHA,
    "profit_factor":    1.25,
    "win_rate":         0.50,
    "both_halves_pos":  True,
}


# ── Data Loading ───────────────────────────────────────────────────
def load_and_resample(path: str, source_tz: str, col_ts: str) -> pd.DataFrame:
    """Load 1-min bars, filter RTH, resample to daily."""
    print(f"Loading {path} ...")
    df = pd.read_csv(
        path,
        usecols=[col_ts, "open", "high", "low", "close", "volume"],
        parse_dates=[col_ts],
    )
    df = df.rename(columns={col_ts: "timestamp"})
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Localize to source tz, convert to ET
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(source_tz)
    df["timestamp"] = df["timestamp"].dt.tz_convert("US/Eastern")

    # Filter RTH: 09:30 - 16:00 ET
    t = df["timestamp"].dt.time
    import datetime as _dt
    rth = (t >= _dt.time(9, 30)) & (t < _dt.time(16, 0))
    df = df.loc[rth].copy()

    # Resample to daily: open/high/low/close/volume
    df = df.set_index("timestamp")
    daily = df.resample("1D").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["open"])

    daily.index = daily.index.date
    daily.index = pd.DatetimeIndex(daily.index)
    daily.index.name = "date"

    print(f"  {len(daily)} RTH daily bars  {daily.index[0].date()} -> {daily.index[-1].date()}")
    return daily


# ── Turn-of-Month Detection ───────────────────────────────────────
def label_tom_days(daily: pd.DataFrame) -> pd.DataFrame:
    """
    For each trading day, compute:
      - tdays_to_month_end: trading days remaining in current month (0 = last day)
      - tdays_from_month_start: trading days since month start (0 = first day)
    """
    df = daily.copy()
    df["year_month"] = df.index.to_period("M")

    # Trading day index within each month
    df["tday_in_month"] = df.groupby("year_month").cumcount()
    month_sizes = df.groupby("year_month")["tday_in_month"].transform("count")
    df["tdays_to_end"] = month_sizes - df["tday_in_month"] - 1  # 0 = last day
    df["tdays_from_start"] = df["tday_in_month"]  # 0 = first day

    return df


def generate_trades(df: pd.DataFrame, entry_before: int, exit_after: int,
                    cost: float) -> pd.DataFrame:
    """
    Generate turn-of-month trades.
    Entry: close of day at T-entry_before (entry_before trading days before month end).
    Exit:  close of day at T+exit_after (exit_after trading days after month start).
    """
    trades = []
    months = df["year_month"].unique()

    for i, month in enumerate(months):
        month_data = df[df["year_month"] == month]

        # Find entry day: entry_before days before month end
        entry_candidates = month_data[month_data["tdays_to_end"] == entry_before - 1]
        # tdays_to_end == 0 is last day, == 1 is second-to-last, etc.
        # "T-2" means enter 2 days before end → tdays_to_end == 1
        # Actually: T-N means N trading days before last day
        # T-2: enter at close of day where tdays_to_end == 1 (2nd to last)
        # T-1: enter at close of day where tdays_to_end == 0 (last day)
        entry_candidates = month_data[month_data["tdays_to_end"] == entry_before - 1]

        if len(entry_candidates) == 0:
            continue

        entry_day = entry_candidates.index[-1]
        entry_price = df.loc[entry_day, "close"]

        # Find exit day: exit_after days into next month
        if i + 1 >= len(months):
            continue
        next_month = months[i + 1]
        next_month_data = df[df["year_month"] == next_month]

        # T+3 means 3rd trading day → tdays_from_start == 2
        exit_candidates = next_month_data[next_month_data["tdays_from_start"] == exit_after - 1]

        if len(exit_candidates) == 0:
            continue

        exit_day = exit_candidates.index[0]
        exit_price = df.loc[exit_day, "close"]

        pnl = exit_price - entry_price - cost
        trades.append({
            "entry_date": entry_day,
            "exit_date":  exit_day,
            "entry_price": entry_price,
            "exit_price":  exit_price,
            "pnl_pts":     pnl,
            "hold_days":   (exit_day - entry_day).days,
        })

    return pd.DataFrame(trades)


# ── Evaluation ─────────────────────────────────────────────────────
def evaluate(trades: pd.DataFrame, label: str, alpha: float) -> dict:
    """Evaluate a set of trades and print results."""
    if len(trades) == 0:
        print(f"  [{label}] No trades generated.")
        return {"pass": False}

    pnl = trades["pnl_pts"].values
    n = len(pnl)
    mean_pnl = np.mean(pnl)
    median_pnl = np.median(pnl)

    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    wr = len(wins) / n if n > 0 else 0

    gross_profit = np.sum(wins) if len(wins) > 0 else 0
    gross_loss = np.abs(np.sum(losses)) if len(losses) > 0 else 1e-9
    pf = gross_profit / gross_loss

    avg_loss = np.mean(np.abs(losses)) if len(losses) > 0 else 1e-9

    # Equity curve and drawdown
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    max_dd = np.min(dd)

    # One-sided t-test (H_a: mean > 0)
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

    # Annualized Sharpe (approximate: ~12 trades/year for TOM)
    trades_per_year = 12
    if np.std(pnl) > 0:
        sharpe_per_trade = np.mean(pnl) / np.std(pnl)
        sharpe_ann = sharpe_per_trade * np.sqrt(trades_per_year)
    else:
        sharpe_ann = 0
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

    print(f"\n  [{tag}] {label}")
    print(f"    n={n:3d}  mean={mean_pnl:+.4f}pts  median={median_pnl:+.4f}pts"
          f"  WR={wr*100:.1f}%  PF={pf:.3f}  p={p_value:.5f}")
    print(f"    MaxDD={abs(max_dd):.1f}pts  AvgLoss={avg_loss:.2f}pts"
          f"  DD/AvgLoss={abs(max_dd)/avg_loss:.1f}"
          f"  Sharpe[H1/H2]={h1_sharpe:.3f}/{h2_sharpe:.3f}")
    if failures:
        print(f"    FAILED: {', '.join(failures)}")

    # Annual breakdown
    trades_copy = trades.copy()
    trades_copy["year"] = pd.to_datetime(trades_copy["entry_date"]).dt.year
    print("    Annual:")
    for year, grp in trades_copy.groupby("year"):
        yr_pnl = grp["pnl_pts"]
        print(f"      {year}: n={len(yr_pnl):2d}  mean={yr_pnl.mean():+.2f}pts"
              f"  total={yr_pnl.sum():+.1f}pts")

    return {
        "pass": passed,
        "n": n, "mean": mean_pnl, "wr": wr, "pf": pf,
        "p_value": p_value, "max_dd": max_dd,
        "sharpe_h1": h1_sharpe, "sharpe_h2": h2_sharpe,
        "failures": failures,
    }


# ── Diagnostic ─────────────────────────────────────────────────────
def run_diagnostic(daily: pd.DataFrame):
    """Compare TOM window returns vs rest-of-month returns."""
    df = label_tom_days(daily)
    df["daily_ret"] = df["close"].pct_change() * 100  # percent

    # TOM window: last 2 days of month + first 3 days of next month
    tom_mask = (df["tdays_to_end"] <= 1) | (df["tdays_from_start"] <= 2)
    tom_days = df[tom_mask]["daily_ret"].dropna()
    non_tom_days = df[~tom_mask]["daily_ret"].dropna()

    print("\n" + "=" * 70)
    print("  DIAGNOSTIC -- TOM vs non-TOM daily returns (full sample)")
    print("=" * 70)
    print(f"  TOM days:     n={len(tom_days):4d}  mean={tom_days.mean():+.4f}%"
          f"  std={tom_days.std():.4f}%")
    print(f"  Non-TOM days: n={len(non_tom_days):4d}  mean={non_tom_days.mean():+.4f}%"
          f"  std={non_tom_days.std():.4f}%")
    t, p = stats.ttest_ind(tom_days, non_tom_days)
    print(f"  Difference t-test: t={t:.3f}  p={p:.4f}")


# ── Main ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="H7: Turn-of-Month Effect")
    parser.add_argument("--input", required=True, help="Path to ES 1-min CSV")
    parser.add_argument("--source-tz", default="utc")
    parser.add_argument("--col-timestamp", default="ts_event")
    args = parser.parse_args()

    daily = load_and_resample(args.input, args.source_tz, args.col_timestamp)
    df = label_tom_days(daily)

    # Split
    is_data = df[df.index <= IS_END]
    oos_data = df[df.index >= OOS_START]

    n_months_is = is_data["year_month"].nunique()
    n_months_oos = oos_data["year_month"].nunique()

    print(f"\n{'='*70}")
    print(f"  TURN-OF-MONTH EFFECT -- HYPOTHESIS 7")
    print(f"{'='*70}")
    print(f"  Calendar anomaly. Long-only around month boundaries.")
    print(f"  Academic basis: Ariel (1987), Lakonishok & Smidt (1988)")
    print(f"  IS months: {n_months_is}  |  OOS months: {n_months_oos}")
    print(f"  Variants: {len(VARIANTS)}  |  Bonferroni alpha: {BONFERRONI_ALPHA:.4f}")
    print(f"  Cost: MES {COST_MES}pts/RT")

    # Diagnostic
    run_diagnostic(daily)

    # ── IS ──────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  IN-SAMPLE RESULTS (2010 -- 2018)")
    print(f"{'='*70}")

    is_results = {}
    for v in VARIANTS:
        trades = generate_trades(is_data, v["entry_before"], v["exit_after"], COST_MES)
        result = evaluate(trades, f"IS  | {v['name']}", BONFERRONI_ALPHA)
        is_results[v["name"]] = result

    # ── OOS ─────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  OUT-OF-SAMPLE RESULTS (2019 -- 2024)")
    print(f"{'='*70}")

    oos_results = {}
    for v in VARIANTS:
        trades = generate_trades(oos_data, v["entry_before"], v["exit_after"], COST_MES)
        result = evaluate(trades, f"OOS | {v['name']}", BONFERRONI_ALPHA)
        oos_results[v["name"]] = result

    # ── Verdict ─────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  VERDICT")
    print(f"{'='*70}")

    # Must pass BOTH IS and OOS
    survivors = []
    for v in VARIANTS:
        name = v["name"]
        if is_results[name]["pass"] and oos_results[name]["pass"]:
            survivors.append(name)

    if survivors:
        print(f"\n  SIGNAL DETECTED -- {len(survivors)} variant(s) passed IS+OOS:")
        for s in survivors:
            r = oos_results[s]
            print(f"    -> {s}")
            print(f"       mean={r['mean']:+.4f}pts  WR={r['wr']*100:.1f}%"
                  f"  PF={r['pf']:.3f}  p={r['p_value']:.5f}")
    else:
        # Check if any passed OOS even if IS failed (like H6 pattern)
        oos_only = [v["name"] for v in VARIANTS if oos_results[v["name"]]["pass"]]
        is_only = [v["name"] for v in VARIANTS if is_results[v["name"]]["pass"]]

        if oos_only:
            print(f"\n  PARTIAL: {len(oos_only)} variant(s) passed OOS but failed IS:")
            for s in oos_only:
                r = oos_results[s]
                print(f"    -> {s}  (OOS only)")
                print(f"       mean={r['mean']:+.4f}pts  WR={r['wr']*100:.1f}%"
                      f"  PF={r['pf']:.3f}  p={r['p_value']:.5f}")
            print(f"  NOTE: IS failure suggests effect may be unstable or post-2018 only.")
        elif is_only:
            print(f"\n  PARTIAL: {len(is_only)} variant(s) passed IS but failed OOS:")
            for s in is_only:
                r = is_results[s]
                print(f"    -> {s}  (IS only)")
                print(f"       mean={r['mean']:+.4f}pts  WR={r['wr']*100:.1f}%"
                      f"  PF={r['pf']:.3f}  p={r['p_value']:.5f}")
            print(f"  Effect has likely decayed.")
        else:
            print(f"\n  NO SIGNAL. All variants failed both IS and OOS.")
            print(f"  REJECT Hypothesis 7.")

    # ── ES cost reference ───────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  REFERENCE: ES cost comparison (best OOS variant)")
    print(f"{'='*70}")

    # Find best OOS variant by mean PnL
    best_name = max(oos_results, key=lambda k: oos_results[k].get("mean", -999))
    best_v = [v for v in VARIANTS if v["name"] == best_name][0]
    trades_es = generate_trades(
        oos_data, best_v["entry_before"], best_v["exit_after"], COST_ES
    )
    evaluate(trades_es, f"OOS | {best_name} (ES cost)", BONFERRONI_ALPHA)

    print(f"\n{'='*70}")
    print(f"  COMPLETE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()