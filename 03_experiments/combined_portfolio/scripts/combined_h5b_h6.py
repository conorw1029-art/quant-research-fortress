#!/usr/bin/env python3
"""
COMBINED PORTFOLIO: H5b RSI Mean-Reversion + H6 FOMC Drift
============================================================
Purpose: Merge the two surviving signals into a single equity curve
         and evaluate whether the combined system is viable for
         Topstep paper trading.

H5b: RSI mean-reversion, 5-min bars, RSI(14) oversold=25/overbought=75,
     R:R=1.0, 60-min timeout, 1.5x ATR stop, MES costs.
     ~150 trades/year, small per-trade edge.

H6:  FOMC prior-close -> post-announcement, long-only.
     ~8 trades/year, large per-trade edge.

Conflict rule: H6 takes priority on FOMC days. H5b trades all other days.

Cost: MES 0.52 pts/RT for both.
Period: OOS only (2019-2024) since that's where both signals were validated.
        Also show IS for context.
"""

import argparse
import datetime as dt
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats


# ══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════
COST_MES = 0.52

IS_END    = "2018-12-31"
OOS_START = "2019-01-01"

# H5b parameters (best variant from H5b testing)
RSI_PERIOD = 14
RSI_OVERSOLD = 25
RSI_OVERBOUGHT = 75
RR_RATIO = 1.0
ATR_PERIOD = 20
ATR_STOP_MULT = 1.5
TIMEOUT_BARS = 12  # 60 min on 5-min bars
BAR_TF = "5min"

# FOMC dates (2010-2026) — announcement dates only
# Source: Federal Reserve calendar
FOMC_DATES = [
    # 2010
    "2010-01-27","2010-03-16","2010-04-28","2010-06-23","2010-08-10",
    "2010-09-21","2010-11-03","2010-12-14",
    # 2011
    "2011-01-26","2011-03-15","2011-04-27","2011-06-22","2011-08-09",
    "2011-09-21","2011-11-02","2011-12-13",
    # 2012
    "2012-01-25","2012-03-13","2012-04-25","2012-06-20","2012-08-01",
    "2012-09-13","2012-10-24","2012-12-12",
    # 2013
    "2013-01-30","2013-03-20","2013-05-01","2013-06-19","2013-07-31",
    "2013-09-18","2013-10-30","2013-12-18",
    # 2014
    "2014-01-29","2014-03-19","2014-04-30","2014-06-18","2014-07-30",
    "2014-09-17","2014-10-29","2014-12-17",
    # 2015
    "2015-01-28","2015-03-18","2015-04-29","2015-06-17","2015-07-29",
    "2015-09-17","2015-10-28","2015-12-16",
    # 2016
    "2016-01-27","2016-03-16","2016-04-27","2016-06-15","2016-07-27",
    "2016-09-21","2016-11-02","2016-12-14",
    # 2017
    "2017-02-01","2017-03-15","2017-05-03","2017-06-14","2017-07-26",
    "2017-09-20","2017-11-01","2017-12-13",
    # 2018
    "2018-01-31","2018-03-21","2018-05-02","2018-06-13","2018-08-01",
    "2018-09-26","2018-11-08","2018-12-19",
    # 2019
    "2019-01-30","2019-03-20","2019-05-01","2019-06-19","2019-07-31",
    "2019-09-18","2019-10-30","2019-12-11",
    # 2020
    "2020-01-29","2020-03-03","2020-03-15","2020-04-29","2020-06-10",
    "2020-07-29","2020-09-16","2020-11-05","2020-12-16",
    # 2021
    "2021-01-27","2021-03-17","2021-04-28","2021-06-16","2021-07-28",
    "2021-09-22","2021-11-03","2021-12-15",
    # 2022
    "2022-01-26","2022-03-16","2022-05-04","2022-06-15","2022-07-27",
    "2022-09-21","2022-11-02","2022-12-14",
    # 2023
    "2023-02-01","2023-03-22","2023-05-03","2023-06-14","2023-07-26",
    "2023-09-20","2023-11-01","2023-12-13",
    # 2024
    "2024-01-31","2024-03-20","2024-05-01","2024-06-12","2024-07-31",
    "2024-09-18","2024-11-07","2024-12-18",
    # 2025
    "2025-01-29","2025-03-19","2025-05-07","2025-06-18","2025-07-30",
    "2025-09-17","2025-10-29","2025-12-17",
    # 2026
    "2026-01-28","2026-03-18","2026-04-29",
]
FOMC_SET = set(pd.to_datetime(d).date() for d in FOMC_DATES)


# ══════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════
def load_data(path: str, source_tz: str, col_ts: str) -> pd.DataFrame:
    """Load 1-min bars, filter RTH."""
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

    t = df["timestamp"].dt.time
    rth = (t >= dt.time(9, 30)) & (t < dt.time(16, 0))
    df = df.loc[rth].copy()
    df["date"] = df["timestamp"].dt.date
    print(f"  {len(df):,} 1-min RTH bars  {df['date'].iloc[0]} -> {df['date'].iloc[-1]}")
    return df


def resample_5min(df: pd.DataFrame) -> pd.DataFrame:
    """Resample to 5-min bars."""
    df5 = df.set_index("timestamp").resample("5min", label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open"])
    t = df5.index.time
    df5 = df5[(t >= dt.time(9, 30)) & (t < dt.time(16, 0))].copy()
    df5["date"] = df5.index.date
    return df5


def resample_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1-min to daily for FOMC strategy."""
    daily = df.set_index("timestamp").resample("1D").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open"])
    daily.index = daily.index.date
    daily.index = pd.DatetimeIndex(daily.index)
    daily.index.name = "date"
    return daily


# ══════════════════════════════════════════════════════════════════
# H5b: RSI MEAN-REVERSION
# ══════════════════════════════════════════════════════════════════
def compute_rsi(series: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def run_h5b(bars: pd.DataFrame, fomc_dates: set, cost: float) -> pd.DataFrame:
    """
    Run H5b RSI mean-reversion strategy.
    Skip FOMC days (H6 takes priority).
    Returns DataFrame of trades with date and pnl_pts.
    """
    df = bars.copy()
    df["rsi"] = compute_rsi(df["close"], RSI_PERIOD)

    # ATR
    df["prev_close"] = df["close"].shift(1)
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            np.abs(df["high"] - df["prev_close"]),
            np.abs(df["low"] - df["prev_close"])
        )
    )
    df["atr"] = df["tr"].rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    df = df.dropna(subset=["rsi", "atr"]).copy()

    trades = []
    dates_traded = set()
    date_groups = {d: grp for d, grp in df.groupby("date")}

    for date in sorted(date_groups.keys()):
        # Skip FOMC days
        if date in fomc_dates:
            continue
        if date in dates_traded:
            continue

        day = date_groups[date]
        if len(day) < 10:
            continue

        for idx in range(1, len(day) - TIMEOUT_BARS):
            if date in dates_traded:
                break

            bar = day.iloc[idx]
            prev_bar = day.iloc[idx - 1]

            if bar["atr"] <= 0 or np.isnan(bar["atr"]):
                continue

            atr = bar["atr"]
            direction = 0

            # Oversold -> long
            if prev_bar["rsi"] >= RSI_OVERSOLD and bar["rsi"] < RSI_OVERSOLD:
                direction = 1
            # Overbought -> short
            elif prev_bar["rsi"] <= RSI_OVERBOUGHT and bar["rsi"] > RSI_OVERBOUGHT:
                direction = -1

            if direction == 0:
                continue

            entry_price = bar["close"]
            stop_dist = ATR_STOP_MULT * atr
            target_dist = RR_RATIO * stop_dist

            target_price = entry_price + direction * target_dist
            stop_price = entry_price - direction * stop_dist

            # Simulate forward
            remaining = day.iloc[idx + 1: idx + 1 + TIMEOUT_BARS]
            exit_price = None

            for _, fb in remaining.iterrows():
                if direction == 1:
                    if fb["low"] <= stop_price:
                        exit_price = stop_price; break
                    if fb["high"] >= target_price:
                        exit_price = target_price; break
                else:
                    if fb["high"] >= stop_price:
                        exit_price = stop_price; break
                    if fb["low"] <= target_price:
                        exit_price = target_price; break

            if exit_price is None:
                if len(remaining) > 0:
                    exit_price = remaining.iloc[-1]["close"]
                else:
                    continue

            pnl = direction * (exit_price - entry_price) - cost
            trades.append({
                "date": date,
                "pnl_pts": pnl,
                "strategy": "H5b",
                "direction": direction,
            })
            dates_traded.add(date)
            break  # one trade per day

    return pd.DataFrame(trades)


# ══════════════════════════════════════════════════════════════════
# H6: FOMC DRIFT
# ══════════════════════════════════════════════════════════════════
def run_h6(daily: pd.DataFrame, fomc_dates: set, cost: float) -> pd.DataFrame:
    """
    Run H6 FOMC drift: buy prior close, sell at FOMC-day close.
    Best variant: prior_close -> post_fomc (approximated as FOMC day close
    since we don't have exact announcement timestamps in this script).
    
    Simplified: buy close of day before FOMC, sell close of FOMC day.
    """
    trades = []
    dates_sorted = daily.index.sort_values()

    for fomc_date in sorted(fomc_dates):
        fomc_ts = pd.Timestamp(fomc_date)

        # Find the trading day before FOMC
        prior_days = dates_sorted[dates_sorted < fomc_ts]
        if len(prior_days) == 0:
            continue

        prior_day = prior_days[-1]

        # Check FOMC day exists in data
        if fomc_ts not in daily.index:
            continue

        entry_price = daily.loc[prior_day, "close"]
        exit_price = daily.loc[fomc_ts, "close"]

        pnl = (exit_price - entry_price) - cost  # long only
        trades.append({
            "date": fomc_date,
            "pnl_pts": pnl,
            "strategy": "H6",
            "direction": 1,
        })

    return pd.DataFrame(trades)


# ══════════════════════════════════════════════════════════════════
# PORTFOLIO ANALYSIS
# ══════════════════════════════════════════════════════════════════
def analyze_portfolio(trades: pd.DataFrame, label: str):
    """Full analysis of combined trade stream."""
    if len(trades) == 0:
        print(f"  [{label}] No trades.")
        return

    pnl = trades["pnl_pts"].values
    n = len(pnl)
    mean_pnl = np.mean(pnl)
    total_pnl = np.sum(pnl)

    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    wr = len(wins) / n
    gross_profit = np.sum(wins) if len(wins) > 0 else 0
    gross_loss = np.abs(np.sum(losses)) if len(losses) > 0 else 1e-9
    pf = gross_profit / gross_loss
    avg_win = np.mean(wins) if len(wins) > 0 else 0
    avg_loss_val = np.mean(np.abs(losses)) if len(losses) > 0 else 0

    # Equity curve
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    max_dd = np.min(dd)
    max_dd_idx = np.argmin(dd)

    # Find drawdown duration
    dd_durations = []
    in_dd = False
    dd_start = 0
    for i in range(len(dd)):
        if dd[i] < 0 and not in_dd:
            in_dd = True
            dd_start = i
        elif dd[i] >= 0 and in_dd:
            in_dd = False
            dd_durations.append(i - dd_start)
    avg_dd_dur = np.mean(dd_durations) if dd_durations else 0

    # Sharpe
    trades_copy = trades.copy()
    trades_copy["year"] = pd.to_datetime(trades_copy["date"]).dt.year
    n_years = trades_copy["year"].nunique()
    trades_per_year = n / max(n_years, 1)

    sharpe_per_trade = mean_pnl / np.std(pnl) if np.std(pnl) > 0 else 0
    sharpe_ann = sharpe_per_trade * np.sqrt(trades_per_year)

    # Both halves
    half = n // 2
    h1_pnl = pnl[:half]
    h2_pnl = pnl[half:]
    h1_sharpe = (np.mean(h1_pnl) / np.std(h1_pnl) * np.sqrt(trades_per_year)
                 if np.std(h1_pnl) > 0 else 0)
    h2_sharpe = (np.mean(h2_pnl) / np.std(h2_pnl) * np.sqrt(trades_per_year)
                 if np.std(h2_pnl) > 0 else 0)

    # t-test
    if np.std(pnl) > 0:
        t_stat, p_two = stats.ttest_1samp(pnl, 0)
        p_value = p_two / 2 if t_stat > 0 else 1.0 - p_two / 2
    else:
        p_value = 1.0

    # Max consecutive losses
    max_consec_loss = 0
    curr_consec = 0
    for p in pnl:
        if p <= 0:
            curr_consec += 1
            max_consec_loss = max(max_consec_loss, curr_consec)
        else:
            curr_consec = 0

    print(f"\n  -- {label} --")
    print(f"  Total trades:     {n}")
    print(f"  Total PnL:        {total_pnl:+.1f} pts")
    print(f"  Mean per trade:   {mean_pnl:+.4f} pts")
    print(f"  Win rate:         {wr*100:.1f}%")
    print(f"  Profit factor:    {pf:.3f}")
    print(f"  Avg win:          {avg_win:+.4f} pts")
    print(f"  Avg loss:         {avg_loss_val:.4f} pts")
    print(f"  Max drawdown:     {abs(max_dd):.1f} pts")
    print(f"  Avg DD duration:  {avg_dd_dur:.0f} trades")
    print(f"  Max consec loss:  {max_consec_loss}")
    print(f"  Sharpe (ann):     {sharpe_ann:.3f}")
    print(f"  Sharpe H1/H2:    {h1_sharpe:.3f} / {h2_sharpe:.3f}")
    print(f"  p-value:          {p_value:.5f}")
    print(f"  Trades/year:      {trades_per_year:.0f}")

    # Per-strategy breakdown
    print(f"\n  Strategy breakdown:")
    for strat, grp in trades.groupby("strategy"):
        s_pnl = grp["pnl_pts"]
        s_wr = (s_pnl > 0).mean()
        print(f"    {strat}: n={len(grp):4d}  total={s_pnl.sum():+.1f}pts"
              f"  mean={s_pnl.mean():+.4f}pts  WR={s_wr*100:.1f}%")

    # Annual breakdown
    print(f"\n  Annual breakdown:")
    print(f"  {'Year':>6s} {'n':>5s} {'H5b_n':>6s} {'H6_n':>5s}"
          f" {'Total':>10s} {'Mean':>10s} {'MaxDD':>8s}")
    print(f"  {'-'*58}")

    for year, grp in trades_copy.groupby("year"):
        yr_pnl = grp["pnl_pts"].values
        yr_equity = np.cumsum(yr_pnl)
        yr_peak = np.maximum.accumulate(yr_equity)
        yr_maxdd = np.min(yr_equity - yr_peak)
        h5b_n = len(grp[grp["strategy"] == "H5b"])
        h6_n = len(grp[grp["strategy"] == "H6"])
        print(f"  {year:6d} {len(grp):5d} {h5b_n:6d} {h6_n:5d}"
              f" {yr_pnl.sum():+10.1f} {yr_pnl.mean():+10.4f} {abs(yr_maxdd):8.1f}")

    # Monthly breakdown for more granular view
    trades_copy["month"] = pd.to_datetime(trades_copy["date"]).dt.to_period("M")
    monthly = trades_copy.groupby("month")["pnl_pts"].sum()
    pos_months = (monthly > 0).sum()
    neg_months = (monthly <= 0).sum()
    print(f"\n  Monthly: {pos_months} positive / {neg_months} negative"
          f" ({100*pos_months/(pos_months+neg_months):.0f}% hit rate)")
    print(f"  Best month:  {monthly.max():+.1f} pts")
    print(f"  Worst month: {monthly.min():+.1f} pts")

    # -- Topstep viability check --------------------------------
    print(f"\n  -- TOPSTEP VIABILITY CHECK --")
    # Topstep 50k: max trailing DD = $2000 = 40 MES pts
    # Topstep 50k: profit target = $3000 = 60 MES pts (for funded)
    # Topstep consistency: no single day > 40% of total profit
    # (Using MES point equivalents)

    topstep_max_dd = 40.0  # pts for MES
    topstep_target = 60.0  # pts for funded challenge

    print(f"  Max DD ({abs(max_dd):.1f} pts) vs Topstep limit ({topstep_max_dd} pts):"
          f"  {'PASS' if abs(max_dd) <= topstep_max_dd else 'FAIL'}")

    # Check if any year achieves profit target
    yearly_totals = trades_copy.groupby("year")["pnl_pts"].sum()
    print(f"  Years hitting {topstep_target}-pt target:"
          f"  {(yearly_totals >= topstep_target).sum()} / {len(yearly_totals)}")

    # Consistency: max single-day contribution
    daily_pnl = trades_copy.groupby("date")["pnl_pts"].sum()
    if total_pnl > 0:
        max_day_pct = daily_pnl.max() / total_pnl * 100
        print(f"  Max single-day % of total profit: {max_day_pct:.1f}%"
              f"  {'PASS' if max_day_pct <= 40 else 'FAIL (consistency rule)'}")

    # Expected time to target
    if mean_pnl > 0:
        trades_to_target = topstep_target / mean_pnl
        days_to_target = trades_to_target  # ~1 trade/day
        print(f"  Expected trades to target: {trades_to_target:.0f}"
              f" (~{days_to_target/21:.1f} months)")

    return {
        "n": n, "total": total_pnl, "mean": mean_pnl, "wr": wr,
        "pf": pf, "max_dd": max_dd, "sharpe": sharpe_ann,
        "p_value": p_value,
    }


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Combined H5b + H6 Portfolio")
    parser.add_argument("--input", required=True, help="Path to ES 1-min CSV")
    parser.add_argument("--source-tz", default="utc")
    parser.add_argument("--col-timestamp", default="ts_event")
    args = parser.parse_args()

    # Load data
    df_1min = load_data(args.input, args.source_tz, args.col_timestamp)
    bars_5min = resample_5min(df_1min)
    daily = resample_daily(df_1min)

    print(f"\n{'='*70}")
    print(f"  COMBINED PORTFOLIO: H5b + H6")
    print(f"{'='*70}")
    print(f"  H5b: RSI mean-rev (MES costs, skip FOMC days)")
    print(f"  H6:  FOMC drift (prior close -> FOMC day close)")
    print(f"  Conflict rule: H6 takes priority on FOMC days")
    print(f"  Cost: MES {COST_MES} pts/RT")

    # -- Run H5b ------------------------------------------------
    print(f"\n  Running H5b (RSI mean-reversion)...")
    h5b_trades = run_h5b(bars_5min, FOMC_SET, COST_MES)
    print(f"  H5b: {len(h5b_trades)} trades generated")

    # -- Run H6 -------------------------------------------------
    print(f"  Running H6 (FOMC drift)...")
    h6_trades = run_h6(daily, FOMC_SET, COST_MES)
    print(f"  H6:  {len(h6_trades)} trades generated")

    # -- Combine ------------------------------------------------
    all_trades = pd.concat([h5b_trades, h6_trades], ignore_index=True)
    all_trades = all_trades.sort_values("date").reset_index(drop=True)
    print(f"  Combined: {len(all_trades)} total trades")

    # Check for date conflicts (should be zero)
    h5b_dates = set(h5b_trades["date"]) if len(h5b_trades) > 0 else set()
    h6_dates = set(h6_trades["date"]) if len(h6_trades) > 0 else set()
    conflicts = h5b_dates & h6_dates
    print(f"  Date conflicts: {len(conflicts)} (should be 0)")

    # -- Split IS/OOS -------------------------------------------
    all_trades["date_ts"] = pd.to_datetime(all_trades["date"])
    is_trades = all_trades[all_trades["date_ts"] <= pd.Timestamp(IS_END)].copy()
    oos_trades = all_trades[all_trades["date_ts"] >= pd.Timestamp(OOS_START)].copy()

    # -- Analyze IS ---------------------------------------------
    print(f"\n{'='*70}")
    print(f"  IN-SAMPLE (2010 -- 2018)")
    print(f"{'='*70}")
    analyze_portfolio(is_trades, "IS Combined")

    # -- Analyze OOS --------------------------------------------
    print(f"\n{'='*70}")
    print(f"  OUT-OF-SAMPLE (2019 -- 2024)")
    print(f"{'='*70}")
    analyze_portfolio(oos_trades, "OOS Combined")

    # -- H5b standalone (for comparison) ------------------------
    print(f"\n{'='*70}")
    print(f"  COMPARISON: H5b STANDALONE (OOS)")
    print(f"{'='*70}")
    h5b_oos = h5b_trades[pd.to_datetime(h5b_trades["date"]) >= pd.Timestamp(OOS_START)]
    analyze_portfolio(h5b_oos.copy(), "H5b Alone OOS")

    # -- H6 standalone (for comparison) -------------------------
    print(f"\n{'='*70}")
    print(f"  COMPARISON: H6 STANDALONE (OOS)")
    print(f"{'='*70}")
    h6_oos = h6_trades[pd.to_datetime(h6_trades["date"]) >= pd.Timestamp(OOS_START)]
    analyze_portfolio(h6_oos.copy(), "H6 Alone OOS")

    # -- Full-sample combined -----------------------------------
    print(f"\n{'='*70}")
    print(f"  FULL SAMPLE (2010 -- 2024)")
    print(f"{'='*70}")
    analyze_portfolio(all_trades.copy(), "Full Sample Combined")

    print(f"\n{'='*70}")
    print(f"  COMPLETE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()