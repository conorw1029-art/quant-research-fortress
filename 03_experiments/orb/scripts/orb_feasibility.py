"""
orb_feasibility.py

Opening Range Breakout (ORB) feasibility study on ES futures 1-minute bars.

Purpose: determine whether an ORB strategy exhibits statistically significant
out-of-sample edge on ES futures after realistic transaction costs, before
committing to build production infrastructure around it.

Pre-registered design — no parameters tuned after data inspection.

Hypothesis
----------
After the first N minutes of the RTH session on ES futures, a close above the
opening range high (below opening range low) leads to directionally-persistent
moves through session end, producing positive expected value net of costs.

Method
------
- Data: ES continuous front-month 1-minute bars exported from NinjaTrader.
- Split: In-sample 2008-2018 (~11yr), Out-of-sample 2019-2024 (~6yr incl COVID).
- Variants tested (grid of 9):
    opening range duration ∈ {5, 15, 30} minutes
    × range-size filter ∈ {none, <0.5 ATR, 0.3-1.0 ATR}
- Rules:
    Entry: first 1-min close beyond OR high/low triggers entry at that close.
    Stop: opposite side of the opening range.
    Exit: 15:55 ET or stop, whichever first.
    Max 1 trade per day.
- Costs: $30/round-turn on ES ($5 commission + 1-tick slippage each side).

Pass/fail on OOS (all required):
    1. At least one variant: mean trade PnL > 0 net costs, Bonferroni p < 0.0011
    2. Profit factor >= 1.25 net costs
    3. Win rate >= 35%
    4. Max drawdown <= 15 * avg stop distance (in points)
    5. Sharpe positive in both halves of OOS (no regime cliff)

Usage
-----
    python orb_feasibility.py --input ES_1min.csv

Expected input CSV schema (NinjaTrader export defaults):
    DateTime,Open,High,Low,Close,Volume
Column mapping is flexible via CLI flags.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import time as dtime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import pytz
from scipy import stats

NYC = pytz.timezone("America/New_York")

# RTH session for CME equity index futures.
RTH_OPEN = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)
ENTRY_CUTOFF = dtime(15, 0)   # No new entries after 15:00 ET (too little room to run).
FORCED_EXIT = dtime(15, 55)   # Flat all positions by 15:55 ET.

# ES contract spec.
ES_POINT_VALUE = 50.0         # $50 per full point.
ES_TICK_SIZE = 0.25
ES_TICK_VALUE = 12.50
ES_ROUND_TURN_COST_DOLLARS = 30.0   # $5 comm + 1-tick slippage each side.
ES_ROUND_TURN_COST_POINTS = ES_ROUND_TURN_COST_DOLLARS / ES_POINT_VALUE  # 0.6 pts

# Variant grid (pre-registered, not tuned).
OR_DURATIONS_MIN = [5, 15, 30]
RANGE_FILTERS = {
    "no_filter":      (0.0, float("inf")),
    "small_only":     (0.0, 0.5),
    "mid_sized":      (0.3, 1.0),
}
N_VARIANTS = len(OR_DURATIONS_MIN) * len(RANGE_FILTERS)   # 9
BONFERRONI_ALPHA = 0.01 / N_VARIANTS                       # 0.00111

# Data split.
IN_SAMPLE_END = "2018-12-31"
OOS_START = "2019-01-01"
OOS_END = "2024-12-31"

# ATR window for range normalization.
ATR_PERIOD = 20


# --------------------------------------------------------------- Data loading

@dataclass
class ColumnMap:
    timestamp: str = "DateTime"
    open: str = "Open"
    high: str = "High"
    low: str = "Low"
    close: str = "Close"
    volume: str = "Volume"


def load_bars(csv_path: Path, col_map: ColumnMap, source_tz: str) -> pd.DataFrame:
    """Load 1-minute bars, normalize to UTC, label RTH/ETH."""
    print(f"Loading {csv_path}...")
    df = pd.read_csv(csv_path)

    required = {col_map.timestamp, col_map.open, col_map.high,
                col_map.low, col_map.close, col_map.volume}
    missing = required - set(df.columns)
    if missing:
        print(f"ERROR: Missing columns: {missing}")
        print(f"Available columns: {list(df.columns)}")
        sys.exit(1)

    df = df.rename(columns={
        col_map.timestamp: "timestamp",
        col_map.open: "open",
        col_map.high: "high",
        col_map.low: "low",
        col_map.close: "close",
        col_map.volume: "volume",
    })[["timestamp", "open", "high", "low", "close", "volume"]]

    ts = pd.to_datetime(df["timestamp"], errors="coerce")
    if ts.isna().any():
        print(f"ERROR: {ts.isna().sum()} unparseable timestamps")
        sys.exit(1)

    src_tz = pytz.timezone(source_tz)
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(src_tz, ambiguous="infer", nonexistent="shift_forward")
    ts = ts.dt.tz_convert("UTC")

    df["timestamp"] = ts
    df = df.set_index("timestamp").sort_index()

    # Integrity checks.
    dup_count = df.index.duplicated().sum()
    if dup_count > 0:
        print(f"WARNING: {dup_count} duplicate timestamps. Keeping first.")
        df = df[~df.index.duplicated(keep="first")]

    nonpos = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    if nonpos.any():
        print(f"ERROR: {nonpos.sum()} rows with non-positive prices")
        sys.exit(1)

    bad_ohlc = (
        (df["high"] < df[["open", "close", "low"]].max(axis=1))
        | (df["low"] > df[["open", "close", "high"]].min(axis=1))
    )
    if bad_ohlc.any():
        print(f"ERROR: {bad_ohlc.sum()} rows with OHLC inconsistency")
        sys.exit(1)

    # Session labels.
    et_idx = df.index.tz_convert(NYC)
    t_of_day = pd.Series(et_idx.time, index=df.index)
    weekday = pd.Series(et_idx.weekday, index=df.index)
    is_rth = (t_of_day >= RTH_OPEN) & (t_of_day < RTH_CLOSE) & (weekday < 5)
    df["session"] = np.where(is_rth, "RTH", "ETH")
    df["trade_date_et"] = et_idx.date

    print(f"  Loaded {len(df):,} bars from {df.index.min()} to {df.index.max()}")
    print(f"  RTH bars: {(df['session']=='RTH').sum():,}")
    print(f"  ETH bars: {(df['session']=='ETH').sum():,}")

    return df


# ------------------------------------------------------------- Daily features

def compute_daily_atr(bars: pd.DataFrame) -> pd.Series:
    """Causal 20-day rolling ATR of daily RTH range, indexed by trade_date_et."""
    rth = bars[bars["session"] == "RTH"]
    daily = rth.groupby("trade_date_et").agg(
        high=("high", "max"),
        low=("low", "min"),
    )
    daily["range"] = daily["high"] - daily["low"]
    # shift(1) FIRST so today's ATR uses only prior days' ranges.
    atr = daily["range"].shift(1).rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    atr.name = "atr_20"
    return atr


# ------------------------------------------------------------- Trade simulation

@dataclass
class Trade:
    date: object
    direction: int          # +1 long, -1 short
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str        # "stop" or "time"
    or_range: float
    pnl_points_gross: float
    pnl_points_net: float


def simulate_day(
    day_bars: pd.DataFrame,
    or_duration_min: int,
    range_filter_bounds: tuple[float, float],
    atr_today: float,
) -> Trade | None:
    """
    Simulate one trading day. Returns Trade or None if no trade taken.

    day_bars: minute bars for a single trade_date, RTH only, sorted.
    """
    if len(day_bars) < 30 or pd.isna(atr_today) or atr_today <= 0:
        return None

    # Localize indices to ET so we can compare wall-clock times.
    et_times = day_bars.index.tz_convert(NYC)
    t_of_day = pd.Series(et_times.time, index=day_bars.index)

    # Opening range: bars with ET time in [9:30, 9:30 + N min).
    or_end = dtime(RTH_OPEN.hour, RTH_OPEN.minute + or_duration_min) \
        if RTH_OPEN.minute + or_duration_min < 60 \
        else dtime(RTH_OPEN.hour + 1, (RTH_OPEN.minute + or_duration_min) % 60)

    or_mask = (t_of_day >= RTH_OPEN) & (t_of_day < or_end)
    or_bars = day_bars[or_mask]

    # Need full opening range.
    if len(or_bars) < or_duration_min:
        return None

    or_high = or_bars["high"].max()
    or_low = or_bars["low"].min()
    or_range = or_high - or_low
    if or_range <= 0:
        return None

    # Range filter: only trade if or_range/ATR falls within bounds.
    range_norm = or_range / atr_today
    lo, hi = range_filter_bounds
    if not (lo <= range_norm < hi):
        return None

    # Scan for first breakout after OR ends, up to entry cutoff.
    post_or_mask = (t_of_day >= or_end) & (t_of_day < ENTRY_CUTOFF)
    scan = day_bars[post_or_mask]

    entry_idx = None
    direction = 0
    for idx, row in scan.iterrows():
        if row["close"] > or_high:
            entry_idx = idx
            direction = 1
            break
        if row["close"] < or_low:
            entry_idx = idx
            direction = -1
            break

    if entry_idx is None:
        return None

    entry_price = scan.loc[entry_idx, "close"]
    stop_price = or_low if direction == 1 else or_high

    # Simulate from next bar onward.
    post_entry_mask = (day_bars.index > entry_idx) & (t_of_day < FORCED_EXIT)
    post_entry = day_bars[post_entry_mask]

    exit_idx = None
    exit_price = None
    exit_reason = None

    for idx, row in post_entry.iterrows():
        # Stop check: if this bar's low (long) or high (short) violates stop,
        # assume fill at stop price. This is slightly optimistic (could gap
        # through), but with 1-min bars on liquid ES, a reasonable assumption.
        if direction == 1 and row["low"] <= stop_price:
            exit_idx = idx
            exit_price = stop_price
            exit_reason = "stop"
            break
        if direction == -1 and row["high"] >= stop_price:
            exit_idx = idx
            exit_price = stop_price
            exit_reason = "stop"
            break

    # If no stop hit, exit at the last bar before FORCED_EXIT (time exit).
    if exit_idx is None:
        if len(post_entry) == 0:
            return None
        last = post_entry.iloc[-1]
        exit_idx = post_entry.index[-1]
        exit_price = last["close"]
        exit_reason = "time"

    pnl_points_gross = direction * (exit_price - entry_price)
    pnl_points_net = pnl_points_gross - ES_ROUND_TURN_COST_POINTS

    return Trade(
        date=day_bars["trade_date_et"].iloc[0],
        direction=direction,
        entry_time=entry_idx,
        entry_price=entry_price,
        exit_time=exit_idx,
        exit_price=exit_price,
        exit_reason=exit_reason,
        or_range=or_range,
        pnl_points_gross=pnl_points_gross,
        pnl_points_net=pnl_points_net,
    )


def run_variant(
    bars: pd.DataFrame,
    atr_series: pd.Series,
    or_duration_min: int,
    range_filter_bounds: tuple[float, float],
) -> pd.DataFrame:
    """Run full simulation for one variant across all days. Returns trades DataFrame."""
    rth = bars[bars["session"] == "RTH"].copy()
    trades = []
    for trade_date, day_bars in rth.groupby("trade_date_et"):
        atr_today = atr_series.get(trade_date, np.nan)
        tr = simulate_day(day_bars, or_duration_min, range_filter_bounds, atr_today)
        if tr is not None:
            trades.append(tr)

    if not trades:
        return pd.DataFrame()

    df = pd.DataFrame([t.__dict__ for t in trades])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


# ------------------------------------------------------------- Performance

def performance_summary(trades: pd.DataFrame, label: str) -> dict:
    """Compute metrics on a set of trades."""
    if len(trades) == 0:
        return {
            "label": label, "n_trades": 0, "mean_pnl_net": np.nan,
            "t_stat": np.nan, "p_value": np.nan, "sharpe": np.nan,
            "win_rate": np.nan, "profit_factor": np.nan, "max_dd_pts": np.nan,
            "avg_stop_pts": np.nan,
        }

    net = trades["pnl_points_net"]
    n = len(net)
    mean = net.mean()
    std = net.std()
    t_stat, p_two = stats.ttest_1samp(net, 0.0) if n >= 10 else (np.nan, np.nan)
    p_one = p_two / 2 if not np.isnan(p_two) and t_stat > 0 else (1 - p_two / 2 if not np.isnan(p_two) else np.nan)

    wins = (net > 0).sum()
    win_rate = wins / n
    gross_wins = net[net > 0].sum()
    gross_losses = -net[net < 0].sum()
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else np.inf

    cumul = net.cumsum()
    running_max = cumul.cummax()
    drawdown = cumul - running_max
    max_dd = drawdown.min()   # negative number

    # Average stop distance in points (for drawdown normalization).
    # Stop distance = direction * (entry - stop_price). For long: entry - or_low;
    # for short: or_high - entry. We can recover approximately from or_range:
    # worst case stop distance is the full or_range.
    avg_stop = trades["or_range"].mean()

    # Daily-trade Sharpe (annualized, assuming ~252 trading days and
    # approximately 1 trade per trade-day, which is true for ORB).
    # We use trades-per-year in denominator, not 252, to be conservative.
    trades_per_year = n / ((trades.index.max() - trades.index.min()).days / 365.25) if n > 1 else 0
    sharpe = (mean / std) * np.sqrt(trades_per_year) if std > 0 else np.nan

    return {
        "label": label,
        "n_trades": n,
        "mean_pnl_net": mean,
        "std_pnl_net": std,
        "t_stat": t_stat,
        "p_value_one_sided": p_one,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_dd_pts": max_dd,
        "avg_stop_pts": avg_stop,
        "trades_per_year": trades_per_year,
    }


def check_half_sharpe(trades: pd.DataFrame) -> tuple[float, float]:
    """Split OOS trades in half by date; return (sharpe_first, sharpe_second)."""
    if len(trades) < 20:
        return (np.nan, np.nan)
    mid = trades.index[len(trades) // 2]
    first = trades.loc[:mid]
    second = trades.loc[mid:]

    def _sharpe(t):
        if len(t) < 10 or t["pnl_points_net"].std() == 0:
            return np.nan
        days = (t.index.max() - t.index.min()).days / 365.25
        tpy = len(t) / days if days > 0 else 0
        return (t["pnl_points_net"].mean() / t["pnl_points_net"].std()) * np.sqrt(tpy)

    return (_sharpe(first), _sharpe(second))


# ------------------------------------------------------------- Reporting

def print_variant_table(results: list[dict], title: str) -> None:
    print(f"\n  {title}")
    print(f"  {'variant':<30} {'n':>5} {'mean_pts':>9} {'t':>7} {'p(1s)':>8} "
          f"{'sharpe':>7} {'win%':>6} {'PF':>6} {'DD':>7}")
    print(f"  {'-'*90}")
    for r in results:
        if r["n_trades"] == 0:
            print(f"  {r['label']:<30}   no trades")
            continue
        print(
            f"  {r['label']:<30} {r['n_trades']:>5} "
            f"{r['mean_pnl_net']:>+9.3f} "
            f"{r['t_stat']:>+7.2f} {r['p_value_one_sided']:>8.4f} "
            f"{r['sharpe']:>+7.2f} {r['win_rate']*100:>5.1f}% "
            f"{r['profit_factor']:>6.2f} {r['max_dd_pts']:>+7.2f}"
        )


# ------------------------------------------------------------------ Main

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to ES 1-min CSV")
    parser.add_argument("--source-tz", default="America/New_York")
    parser.add_argument("--col-timestamp", default="DateTime")
    parser.add_argument("--col-open", default="Open")
    parser.add_argument("--col-high", default="High")
    parser.add_argument("--col-low", default="Low")
    parser.add_argument("--col-close", default="Close")
    parser.add_argument("--col-volume", default="Volume")
    args = parser.parse_args()

    col_map = ColumnMap(
        timestamp=args.col_timestamp, open=args.col_open, high=args.col_high,
        low=args.col_low, close=args.col_close, volume=args.col_volume,
    )

    print("=" * 78)
    print("  ORB FEASIBILITY STUDY — ES 1-MIN BARS")
    print("  Pre-registered design. No parameters tuned after data inspection.")
    print("=" * 78)

    bars = load_bars(Path(args.input), col_map, args.source_tz)

    atr = compute_daily_atr(bars)
    print(f"  Computed causal 20-day ATR for {atr.notna().sum()} days")

    # Split.
    is_end_ts = pd.Timestamp(IN_SAMPLE_END, tz="UTC") + pd.Timedelta(days=1)
    oos_start_ts = pd.Timestamp(OOS_START, tz="UTC")
    oos_end_ts = pd.Timestamp(OOS_END, tz="UTC") + pd.Timedelta(days=1)

    in_sample_bars = bars[bars.index < is_end_ts]
    oos_bars = bars[(bars.index >= oos_start_ts) & (bars.index < oos_end_ts)]

    print(f"\n  In-sample bars: {len(in_sample_bars):,}")
    print(f"  OOS bars:       {len(oos_bars):,}")

    # Run all 9 variants on both splits.
    is_results = []
    oos_results = []
    oos_trades_by_variant = {}

    for dur in OR_DURATIONS_MIN:
        for filt_name, filt_bounds in RANGE_FILTERS.items():
            variant_label = f"OR={dur}m, filt={filt_name}"

            is_tr = run_variant(in_sample_bars, atr, dur, filt_bounds)
            oos_tr = run_variant(oos_bars, atr, dur, filt_bounds)

            is_results.append(performance_summary(is_tr, variant_label))
            oos_results.append(performance_summary(oos_tr, variant_label))
            oos_trades_by_variant[variant_label] = oos_tr

    print_variant_table(is_results, "IN-SAMPLE RESULTS (2008–2018)")
    print_variant_table(oos_results, "OUT-OF-SAMPLE RESULTS (2019–2024)")

    # --- Verdict ---
    print(f"\n{'='*78}")
    print(f"  PASS/FAIL EVALUATION — OOS")
    print(f"  Criteria: p<{BONFERRONI_ALPHA:.5f} (Bonferroni), PF>=1.25, WR>=35%,")
    print(f"            max DD <= 15x avg stop, half-Sharpe both >0")
    print(f"{'='*78}")

    any_passed = False
    for r in oos_results:
        if r["n_trades"] == 0:
            continue
        label = r["label"]
        reasons = []
        if r["p_value_one_sided"] > BONFERRONI_ALPHA:
            reasons.append(f"p={r['p_value_one_sided']:.4f}>{BONFERRONI_ALPHA:.4f}")
        if r["profit_factor"] < 1.25:
            reasons.append(f"PF={r['profit_factor']:.2f}<1.25")
        if r["win_rate"] < 0.35:
            reasons.append(f"WR={r['win_rate']*100:.1f}%<35%")
        dd_limit = 15 * r["avg_stop_pts"]
        if abs(r["max_dd_pts"]) > dd_limit:
            reasons.append(f"DD={abs(r['max_dd_pts']):.1f}>{dd_limit:.1f}")

        sh1, sh2 = check_half_sharpe(oos_trades_by_variant[label])
        if not (sh1 > 0 and sh2 > 0):
            reasons.append(f"half-Sharpe=({sh1:.2f},{sh2:.2f})")

        if not reasons:
            print(f"  [PASS] {label}")
            any_passed = True
        else:
            print(f"  [FAIL] {label}: {'; '.join(reasons)}")

    print(f"\n{'='*78}")
    if any_passed:
        print(f"  VERDICT: At least one variant PASSED all OOS criteria.")
        print(f"  Hypothesis is ALIVE. Proceed to next phase.")
    else:
        print(f"  VERDICT: No variant passed all OOS criteria.")
        print(f"  Hypothesis REJECTED. Move to Hypothesis 3.")
    print(f"{'='*78}")


if __name__ == "__main__":
    main()