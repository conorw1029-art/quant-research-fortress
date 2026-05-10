#!/usr/bin/env python3
"""
H9: Volume Climax Reversal — Feasibility Study
=================================================
Thesis: Extreme volume at session price extremes signals exhaustion.
        Large institutional orders completing → price reverts.

Signal:
  - Resample to 5-min bars.
  - Track 20-bar rolling average volume (causal).
  - Track session high/low (expanding within each day).
  - If bar volume > K * avg_vol AND bar high == session high → SHORT (fade).
  - If bar volume > K * avg_vol AND bar low  == session low  → LONG  (fade).
  - Entry: close of climax bar.
  - Exit: target (frac * ATR), stop (1.5 * ATR), or time stop (60 min).
  - Only first signal per day (avoid clustering).

Variants: K={2.0, 2.5, 3.0} x target_atr={0.75, 1.0} = 6
Bonferroni alpha = 0.05/6 = 0.00833
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

VOL_MULTIPLIERS = [2.0, 2.5, 3.0]
TARGET_ATR_FRACS = [0.75, 1.0]
STOP_ATR_FRAC = 1.5
TIME_STOP_BARS = 12  # 12 x 5min = 60 minutes
ATR_PERIOD = 20  # 20-bar ATR on 5-min bars (causal)
VOL_AVG_PERIOD = 20

N_VARIANTS = len(VOL_MULTIPLIERS) * len(TARGET_ATR_FRACS)
BONFERRONI_ALPHA = 0.05 / N_VARIANTS

CRITERIA = {
    "p_value":         BONFERRONI_ALPHA,
    "profit_factor":   1.25,
    "win_rate":        0.50,
    "both_halves_pos": True,
}


# ── Data Loading ───────────────────────────────────────────────────
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
    """Resample 1-min to 5-min bars."""
    df = df.set_index("timestamp")
    bars = df.resample("5min", label="left", closed="left").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["open"])

    # Re-filter to RTH only (resampling can create edge bars)
    t = bars.index.time
    bars = bars[(t >= dt.time(9, 30)) & (t < dt.time(16, 0))].copy()
    bars["date"] = bars.index.date

    print(f"  {len(bars):,} 5-min RTH bars")
    return bars


# ── Feature Engineering ───────────────────────────────────────────
def add_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Add rolling volume avg, ATR, session high/low."""
    df = bars.copy()

    # Rolling average volume (causal: shift not needed since we compare current bar)
    df["vol_avg"] = df["volume"].rolling(VOL_AVG_PERIOD, min_periods=VOL_AVG_PERIOD).mean()

    # True Range and ATR (on 5-min bars)
    df["prev_close"] = df["close"].shift(1)
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            np.abs(df["high"] - df["prev_close"]),
            np.abs(df["low"] - df["prev_close"])
        )
    )
    df["atr"] = df["tr"].rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()

    # Session high/low (expanding within each day)
    df["session_high"] = df.groupby("date")["high"].cummax()
    df["session_low"] = df.groupby("date")["low"].cummin()

    # Bar position within session
    df["bar_in_session"] = df.groupby("date").cumcount()

    # Drop warmup
    df = df.dropna(subset=["vol_avg", "atr"]).copy()

    return df


# ── Signal Generation & Trade Simulation ──────────────────────────
def generate_trades(df: pd.DataFrame, vol_mult: float, target_atr: float,
                    cost: float) -> pd.DataFrame:
    """
    Generate volume climax reversal trades.
    Only first signal per day.
    """
    trades = []
    dates_traded = set()

    dates = df["date"].unique()
    date_groups = {d: grp for d, grp in df.groupby("date")}

    for date in dates:
        if date in dates_traded:
            continue

        day_bars = date_groups[date]
        if len(day_bars) < 10:  # need enough bars in session
            continue

        for idx in range(5, len(day_bars) - TIME_STOP_BARS):  # skip first 5 bars (25 min warmup)
            bar = day_bars.iloc[idx]

            # Skip if already traded today
            if date in dates_traded:
                break

            # Volume climax check
            if bar["vol_avg"] <= 0 or bar["volume"] < vol_mult * bar["vol_avg"]:
                continue

            # ATR must be valid
            if bar["atr"] <= 0 or np.isnan(bar["atr"]):
                continue

            atr = bar["atr"]
            entry_price = bar["close"]

            # Check if at session extreme
            at_session_high = bar["high"] >= bar["session_high"]
            at_session_low = bar["low"] <= bar["session_low"]

            if not at_session_high and not at_session_low:
                continue

            # Direction: fade the extreme
            if at_session_high and at_session_low:
                # Both (very tight range day or first bar) — skip ambiguous
                continue
            elif at_session_high:
                direction = -1  # short (fade the high)
            else:
                direction = 1   # long (fade the low)

            # Simulate trade: scan forward bars
            target_pts = target_atr * atr
            stop_pts = STOP_ATR_FRAC * atr

            target_price = entry_price + direction * target_pts
            stop_price = entry_price - direction * stop_pts

            exit_price = None
            exit_reason = None
            remaining_bars = day_bars.iloc[idx + 1: idx + 1 + TIME_STOP_BARS]

            for j, (_, future_bar) in enumerate(remaining_bars.iterrows()):
                if direction == 1:  # long
                    if future_bar["low"] <= stop_price:
                        exit_price = stop_price
                        exit_reason = "stop"
                        break
                    if future_bar["high"] >= target_price:
                        exit_price = target_price
                        exit_reason = "target"
                        break
                else:  # short
                    if future_bar["high"] >= stop_price:
                        exit_price = stop_price
                        exit_reason = "stop"
                        break
                    if future_bar["low"] <= target_price:
                        exit_price = target_price
                        exit_reason = "target"
                        break

            # Time stop: exit at close of last bar in window
            if exit_price is None:
                if len(remaining_bars) > 0:
                    exit_price = remaining_bars.iloc[-1]["close"]
                    exit_reason = "time"
                else:
                    continue

            pnl = direction * (exit_price - entry_price) - cost
            trades.append({
                "date": date,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "direction": direction,
                "pnl_pts": pnl,
                "exit_reason": exit_reason,
                "atr": atr,
                "volume_ratio": bar["volume"] / bar["vol_avg"],
            })
            dates_traded.add(date)
            break  # only first signal per day

    return pd.DataFrame(trades)


# ── Evaluation ─────────────────────────────────────────────────────
def evaluate(trades: pd.DataFrame, label: str, alpha: float) -> dict:
    """Evaluate trades, print results."""
    if len(trades) == 0:
        print(f"\n  [FAIL] {label}")
        print(f"    No trades generated.")
        return {"pass": False, "n": 0}

    pnl = trades["pnl_pts"].values
    n = len(pnl)
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

    # Sharpe (approximate trades/year from data)
    trades_df = trades.copy()
    trades_df["year"] = pd.to_datetime(trades_df["date"]).dt.year
    n_years = trades_df["year"].nunique()
    trades_per_year = n / max(n_years, 1)
    sharpe_per_trade = np.mean(pnl) / np.std(pnl) if np.std(pnl) > 0 else 0
    sharpe_ann = sharpe_per_trade * np.sqrt(trades_per_year)
    h1_sharpe = (np.mean(pnl[:half]) / np.std(pnl[:half]) * np.sqrt(trades_per_year)
                 if np.std(pnl[:half]) > 0 else 0)
    h2_sharpe = (np.mean(pnl[half:]) / np.std(pnl[half:]) * np.sqrt(trades_per_year)
                 if np.std(pnl[half:]) > 0 else 0)

    # Exit reason breakdown
    if "exit_reason" in trades.columns:
        reason_counts = trades["exit_reason"].value_counts()
    else:
        reason_counts = pd.Series()

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
    print(f"    n={n:4d}  mean={mean_pnl:+.4f}pts  median={median_pnl:+.4f}pts"
          f"  WR={wr*100:.1f}%  PF={pf:.3f}  p={p_value:.5f}")
    print(f"    MaxDD={abs(max_dd):.1f}pts  AvgLoss={avg_loss:.4f}pts"
          f"  DD/AvgLoss={abs(max_dd)/avg_loss:.1f}"
          f"  Sharpe[H1/H2]={h1_sharpe:.3f}/{h2_sharpe:.3f}")
    if len(reason_counts) > 0:
        reasons_str = "  ".join(f"{k}={v}" for k, v in reason_counts.items())
        print(f"    Exits: {reasons_str}")
    if failures:
        print(f"    FAILED: {', '.join(failures)}")

    # Annual breakdown
    print("    Annual:")
    for year, grp in trades_df.groupby("year"):
        yr_pnl = grp["pnl_pts"]
        print(f"      {year}: n={len(yr_pnl):3d}  mean={yr_pnl.mean():+.4f}pts"
              f"  total={yr_pnl.sum():+.1f}pts")

    # Direction breakdown
    if "direction" in trades.columns:
        for d, d_label in [(1, "LONG"), (-1, "SHORT")]:
            d_trades = trades[trades["direction"] == d]
            if len(d_trades) > 0:
                d_pnl = d_trades["pnl_pts"]
                print(f"    {d_label}: n={len(d_pnl):3d}  mean={d_pnl.mean():+.4f}pts"
                      f"  WR={100*(d_pnl>0).mean():.1f}%")

    return {
        "pass": passed, "n": n, "mean": mean_pnl, "wr": wr, "pf": pf,
        "p_value": p_value, "max_dd": max_dd,
        "h1_sharpe": h1_sharpe, "h2_sharpe": h2_sharpe,
        "failures": failures,
    }


# ── Main ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="H9: Volume Climax Reversal")
    parser.add_argument("--input", required=True, help="Path to ES 1-min CSV")
    parser.add_argument("--source-tz", default="utc")
    parser.add_argument("--col-timestamp", default="ts_event")
    args = parser.parse_args()

    df_1min = load_data(args.input, args.source_tz, args.col_timestamp)
    bars = resample_5min(df_1min)
    df = add_features(bars)

    # Split
    is_df = df[pd.to_datetime(df["date"]) <= pd.Timestamp(IS_END)]
    oos_df = df[pd.to_datetime(df["date"]) >= pd.Timestamp(OOS_START)]

    print(f"\n{'='*70}")
    print(f"  VOLUME CLIMAX REVERSAL -- HYPOTHESIS 9")
    print(f"{'='*70}")
    print(f"  Fade extreme volume at session highs/lows.")
    print(f"  Variants: {N_VARIANTS}  |  Bonferroni alpha: {BONFERRONI_ALPHA:.5f}")
    print(f"  Cost: MES {COST_MES}pts/RT")
    print(f"  IS bars: {len(is_df):,}  |  OOS bars: {len(oos_df):,}")
    print(f"  Stop: {STOP_ATR_FRAC}x ATR  |  Time stop: {TIME_STOP_BARS} bars (60 min)")

    # ── IS Results ──────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  IN-SAMPLE RESULTS (2010 -- 2018)")
    print(f"{'='*70}")

    is_results = {}
    for vol_mult in VOL_MULTIPLIERS:
        for tgt_frac in TARGET_ATR_FRACS:
            name = f"K={vol_mult:.1f} tgt={tgt_frac:.2f}xATR"
            trades = generate_trades(is_df, vol_mult, tgt_frac, COST_MES)
            result = evaluate(trades, f"IS  | {name}", BONFERRONI_ALPHA)
            is_results[name] = result

    # ── OOS Results ─────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  OUT-OF-SAMPLE RESULTS (2019 -- 2024)")
    print(f"{'='*70}")

    oos_results = {}
    for vol_mult in VOL_MULTIPLIERS:
        for tgt_frac in TARGET_ATR_FRACS:
            name = f"K={vol_mult:.1f} tgt={tgt_frac:.2f}xATR"
            trades = generate_trades(oos_df, vol_mult, tgt_frac, COST_MES)
            result = evaluate(trades, f"OOS | {name}", BONFERRONI_ALPHA)
            oos_results[name] = result

    # ── Verdict ─────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  VERDICT")
    print(f"{'='*70}")

    survivors = []
    for name in is_results:
        if is_results[name].get("pass") and oos_results[name].get("pass"):
            survivors.append(name)

    if survivors:
        print(f"\n  SIGNAL DETECTED -- {len(survivors)} variant(s) passed IS+OOS:")
        for s in survivors:
            r = oos_results[s]
            print(f"    -> {s}")
            print(f"       mean={r['mean']:+.4f}pts  WR={r['wr']*100:.1f}%"
                  f"  PF={r['pf']:.3f}  p={r['p_value']:.5f}")
    else:
        # Check partial passes
        oos_pass = [n for n in oos_results if oos_results[n].get("pass")]
        is_pass = [n for n in is_results if is_results[n].get("pass")]

        if oos_pass:
            print(f"\n  PARTIAL: {len(oos_pass)} variant(s) passed OOS but failed IS.")
            for s in oos_pass:
                r = oos_results[s]
                print(f"    -> {s}  mean={r['mean']:+.4f}pts  PF={r['pf']:.3f}"
                      f"  p={r['p_value']:.5f}")
        elif is_pass:
            print(f"\n  PARTIAL: {len(is_pass)} variant(s) passed IS but failed OOS.")
        else:
            print(f"\n  NO SIGNAL. All variants failed.")

        print(f"  REJECT Hypothesis 9.")

    # ── ES cost reference ───────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  REFERENCE: ES cost (best OOS variant)")
    print(f"{'='*70}")

    best_name = max(oos_results, key=lambda k: oos_results[k].get("mean", -999))
    best_parts = best_name.split()
    vol_m = float(best_parts[0].split("=")[1])
    tgt_f = float(best_parts[1].split("=")[1].replace("xATR", ""))
    trades_es = generate_trades(oos_df, vol_m, tgt_f, COST_ES)
    evaluate(trades_es, f"OOS | {best_name} (ES cost)", BONFERRONI_ALPHA)

    print(f"\n{'='*70}")
    print(f"  COMPLETE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()