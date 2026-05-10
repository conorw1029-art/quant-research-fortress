"""
HYPOTHESIS 5: Intraday Mean-Reversion with Dynamic Exit
=========================================================
Structural change from H1-H4: instead of fixed EOD exit, uses
target/stop/timeout exit structure. Tests whether the *trade structure*
(not the signal) was the failure mode in prior hypotheses.

Signal     : RSI-14 on 5-minute bars (simplest mean-reversion oscillator)
Entry      : RSI crosses below oversold -> long; above overbought -> short
Exit       : First of (a) R:R target, (b) ATR stop, (c) time limit
Instrument : ES continuous futures (1-minute bars, resampled to 5-min)
Costs      : $30/round-turn = 1.2 points per round-turn on ES
Date       : 2026-04-24
Status     : PRE-REGISTERED. 12 variants. No post-hoc additions.
"""

import argparse
import warnings
from datetime import time
from itertools import product

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# -----------------------------------------------------------------
# FROZEN PRE-REGISTERED PARAMETERS
# -----------------------------------------------------------------
IS_END       = "2018-12-31"
OOS_START    = "2019-01-01"
OOS_END      = "2024-12-31"

ENTRY_START  = time(9, 45)     # no entries in first 15 min (let RSI warm up)
ENTRY_END    = time(14, 30)    # no entries in last 90 min
FORCE_EXIT   = time(15, 55)    # hard close regardless

COST_PTS     = 1.2             # round-turn ES points
RSI_PERIOD   = 14              # RSI lookback (on 5-min bars)
ATR_PERIOD   = 14              # ATR lookback (on 5-min bars)
STOP_ATR_MULT = 1.5            # fixed -- not searched
MAX_TRADES   = 3               # per day

# 12 variants: oversold x rr_ratio x max_hold
OVERSOLD_LEVELS = [25, 30]     # overbought = 100 - oversold
RR_RATIOS       = [1.0, 1.5, 2.0]
MAX_HOLDS       = [60, 120]    # minutes

N_VARIANTS      = len(OVERSOLD_LEVELS) * len(RR_RATIOS) * len(MAX_HOLDS)
BONFERRONI_P    = 0.10 / N_VARIANTS   # 0.00833
MIN_PROFIT_FACTOR = 1.25
MIN_WIN_RATE      = 0.40
MAX_DD_STOPS      = 20.0


# -----------------------------------------------------------------
# DATA LOADING
# -----------------------------------------------------------------

def load_data(path, col_ts="ts_event", col_open="open", col_high="high",
              col_low="low", col_close="close", col_volume="volume"):
    print(f"Loading {path} ...")
    df = pd.read_csv(path, low_memory=False)
    df["ts"] = pd.to_datetime(df[col_ts], utc=True)
    df = df.set_index("ts").sort_index()
    df.index = df.index.tz_convert("America/New_York")
    df = df.rename(columns={col_open: "open", col_high: "high", col_low: "low",
                             col_close: "close", col_volume: "volume"}
                   )[["open", "high", "low", "close", "volume"]]
    df = df.apply(pd.to_numeric, errors="coerce").dropna()

    # RTH only
    t = df.index.time
    df = df[(t >= time(9, 30)) & (t <= time(15, 59))]
    print(f"  {len(df):,} 1-min RTH bars  {df.index[0].date()} -> {df.index[-1].date()}")
    return df


def resample_5min(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1-min bars to 5-min bars within RTH sessions."""
    bars = df.resample("5min").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()
    # Drop bars outside RTH (resample can create edge artifacts)
    t = bars.index.time
    bars = bars[(t >= time(9, 30)) & (t <= time(15, 55))]
    return bars


# -----------------------------------------------------------------
# FEATURE ENGINEERING (causal, no lookahead)
# -----------------------------------------------------------------

def compute_rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI. Causal: uses only past data."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.clip(lower=1e-10)
    return 100 - (100 / (1 + rs))


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = df.index.date
    df["rsi"] = compute_rsi(df["close"], RSI_PERIOD)
    df["prev_rsi"] = df["rsi"].shift(1)

    # ATR-14 on 5-min bars (causal)
    prev_close = df["close"].shift(1)
    tr = np.maximum(df["high"] - df["low"],
         np.maximum(abs(df["high"] - prev_close),
                    abs(df["low"] - prev_close)))
    df["atr"] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()

    return df.dropna()


# -----------------------------------------------------------------
# BACKTEST ENGINE (1-minute resolution for exits)
# -----------------------------------------------------------------

def run_variant(df_5m: pd.DataFrame, df_1m: pd.DataFrame,
                oversold: int, rr_ratio: float, max_hold: int) -> pd.DataFrame:
    """
    Signal on 5-min bars, manage exits on 1-min bars.

    Entry: RSI crosses below oversold (was above, now below) -> LONG
           RSI crosses above overbought (was below, now above) -> SHORT
    Exit:  (a) target = entry +/- rr_ratio * stop_distance (toward mean)
           (b) stop   = entry -/+ STOP_ATR_MULT * ATR (against trade)
           (c) timeout after max_hold minutes
           (d) force exit at 15:55 ET
    """
    overbought = 100 - oversold

    # Detect RSI crossover signals on 5-min bars
    signals = []
    for ts, row in df_5m.iterrows():
        bar_time = ts.time()
        if bar_time < ENTRY_START or bar_time > ENTRY_END:
            continue
        if np.isnan(row["rsi"]) or np.isnan(row["prev_rsi"]) or np.isnan(row["atr"]):
            continue

        # Long: RSI crosses below oversold (prev >= oversold, now < oversold)
        if row["prev_rsi"] >= oversold and row["rsi"] < oversold:
            signals.append((ts, 1, row["close"], row["atr"]))
        # Short: RSI crosses above overbought
        elif row["prev_rsi"] <= overbought and row["rsi"] > overbought:
            signals.append((ts, -1, row["close"], row["atr"]))

    # Now simulate each signal on 1-min bars
    trades = []
    used_dates = {}  # date -> count

    for sig_ts, direction, entry_price, atr in signals:
        sig_date = sig_ts.date()

        # Max trades per day check
        if used_dates.get(sig_date, 0) >= MAX_TRADES:
            continue

        # Compute stop and target
        stop_dist = STOP_ATR_MULT * atr
        stop_price = entry_price - direction * stop_dist
        target_dist = rr_ratio * stop_dist
        target_price = entry_price + direction * target_dist

        # Timeout timestamp
        timeout_ts = sig_ts + pd.Timedelta(minutes=max_hold)

        # Walk forward on 1-min bars from signal time
        exit_slice = df_1m[(df_1m.index > sig_ts)]

        exit_price = None
        exit_time = None
        exit_reason = None

        for bar_ts, bar in exit_slice.iterrows():
            # Force exit at EOD
            if bar_ts.time() >= FORCE_EXIT:
                exit_price = bar["close"]
                exit_time = bar_ts
                exit_reason = "EOD"
                break

            # Check stop (use bar extremes)
            if direction == 1 and bar["low"] <= stop_price:
                exit_price = stop_price
                exit_time = bar_ts
                exit_reason = "STOP"
                break
            if direction == -1 and bar["high"] >= stop_price:
                exit_price = stop_price
                exit_time = bar_ts
                exit_reason = "STOP"
                break

            # Check target
            if direction == 1 and bar["high"] >= target_price:
                exit_price = target_price
                exit_time = bar_ts
                exit_reason = "TARGET"
                break
            if direction == -1 and bar["low"] <= target_price:
                exit_price = target_price
                exit_time = bar_ts
                exit_reason = "TARGET"
                break

            # Check timeout
            if bar_ts >= timeout_ts:
                exit_price = bar["close"]
                exit_time = bar_ts
                exit_reason = "TIMEOUT"
                break

            # Different day (shouldn't happen with EOD check, but safety)
            if bar_ts.date() != sig_date:
                exit_price = bar["open"]
                exit_time = bar_ts
                exit_reason = "NEXTDAY"
                break

        if exit_price is None:
            continue

        pnl = (exit_price - entry_price) * direction - COST_PTS
        used_dates[sig_date] = used_dates.get(sig_date, 0) + 1

        trades.append({
            "entry_time":  sig_ts,
            "exit_time":   exit_time,
            "entry_price": entry_price,
            "exit_price":  exit_price,
            "direction":   direction,
            "pnl_pts":     pnl,
            "stop_dist":   stop_dist,
            "exit_reason": exit_reason,
            "oversold":    oversold,
            "rr_ratio":    rr_ratio,
            "max_hold":    max_hold,
        })

    if trades:
        return pd.DataFrame(trades)
    return pd.DataFrame(columns=["entry_time", "exit_time", "entry_price",
                                  "exit_price", "direction", "pnl_pts",
                                  "stop_dist", "exit_reason",
                                  "oversold", "rr_ratio", "max_hold"])


# -----------------------------------------------------------------
# METRICS (identical logic to prior scripts)
# -----------------------------------------------------------------

def compute_metrics(trades: pd.DataFrame, label: str) -> dict:
    if len(trades) < 30:
        return {"label": label, "n_trades": len(trades), "PASSED": False,
                "status": "INSUFFICIENT DATA", "mean_pnl": -999}

    pnl = trades["pnl_pts"].values
    n = len(pnl)
    wins = pnl[pnl > 0]; losses = pnl[pnl <= 0]

    mean_pnl = pnl.mean()
    win_rate = len(wins) / n
    gross_win = wins.sum() if len(wins) else 0.0
    gross_los = abs(losses.sum()) if len(losses) else 1e-9
    pf = gross_win / gross_los

    t_stat, p2 = stats.ttest_1samp(pnl, 0)
    p_one = p2 / 2 if t_stat > 0 else 1.0

    equity = np.cumsum(pnl)
    max_dd = abs((equity - np.maximum.accumulate(equity)).min())

    # Avg stop distance from trade data
    stop_trades = trades[trades["exit_reason"] == "STOP"]
    if len(stop_trades) > 0:
        avg_stop = abs(stop_trades["pnl_pts"] + COST_PTS).mean()
    else:
        avg_stop = trades["stop_dist"].mean() if "stop_dist" in trades else abs(pnl).mean()
    avg_stop = max(avg_stop, 0.01)
    dd_ratio = max_dd / avg_stop

    mid = n // 2
    def sharpe(x):
        if len(x) < 2:
            return 0.0
        trades_per_year = len(x) / max((len(pnl) / 500), 1)  # rough annualisation
        return x.mean() / (x.std() + 1e-9) * np.sqrt(max(trades_per_year, 1))
    sh1 = sharpe(pnl[:mid]); sh2 = sharpe(pnl[mid:])

    # Exit reason breakdown
    reasons = trades["exit_reason"].value_counts().to_dict()

    criteria = {
        "mean_pnl_positive": mean_pnl > 0,
        "p_value":           p_one < BONFERRONI_P,
        "profit_factor":     pf >= MIN_PROFIT_FACTOR,
        "win_rate":          win_rate >= MIN_WIN_RATE,
        "dd_ratio":          dd_ratio <= MAX_DD_STOPS,
        "both_halves_pos":   sh1 > 0 and sh2 > 0,
    }
    passed = all(criteria.values())

    return {
        "label": label, "n_trades": n,
        "mean_pnl": round(mean_pnl, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(pf, 3),
        "p_one_sided": round(p_one, 5),
        "max_dd_pts": round(max_dd, 2),
        "avg_stop_pts": round(avg_stop, 2),
        "dd_ratio": round(dd_ratio, 1),
        "sharpe_h1": round(sh1, 3),
        "sharpe_h2": round(sh2, 3),
        "exit_reasons": reasons,
        "criteria": criteria,
        "PASSED": passed,
        "status": "PASS" if passed else "FAIL",
    }


def print_metrics(m: dict) -> None:
    if m.get("status") == "INSUFFICIENT DATA":
        print(f"  {m['label']}: INSUFFICIENT DATA ({m['n_trades']} trades)")
        return
    icon = "PASS" if m["PASSED"] else "FAIL"
    print(f"\n  [{icon}] {m['label']}")
    print(f"    n={m['n_trades']:4d}  mean={m['mean_pnl']:+.4f}pts  "
          f"WR={m['win_rate']:.1%}  PF={m['profit_factor']:.3f}  "
          f"p={m['p_one_sided']:.5f}")
    print(f"    MaxDD={m['max_dd_pts']:.1f}pts  AvgStop={m['avg_stop_pts']:.2f}pts  "
          f"DD/Stop={m['dd_ratio']:.1f}  "
          f"Sharpe[H1/H2]={m['sharpe_h1']:.3f}/{m['sharpe_h2']:.3f}")
    exits = m.get("exit_reasons", {})
    exit_str = "  ".join(f"{k}={v}" for k, v in sorted(exits.items()))
    print(f"    Exits: {exit_str}")
    fails = [k for k, v in m["criteria"].items() if not v]
    if fails:
        print(f"    FAILED: {', '.join(fails)}")


def sep(title=""):
    print(f"\n{'='*70}\n  {title}\n{'='*70}" if title else f"\n{'='*70}")


# -----------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--source-tz",     default="UTC")
    parser.add_argument("--col-timestamp", default="ts_event")
    parser.add_argument("--col-open",      default="open")
    parser.add_argument("--col-high",      default="high")
    parser.add_argument("--col-low",       default="low")
    parser.add_argument("--col-close",     default="close")
    parser.add_argument("--col-volume",    default="volume")
    args = parser.parse_args()

    df_1m = load_data(args.input, args.col_timestamp,
                      args.col_open, args.col_high, args.col_low,
                      args.col_close, args.col_volume)

    print("Resampling to 5-min bars ...")
    df_5m = resample_5min(df_1m)
    df_5m = add_features(df_5m)
    print(f"  {len(df_5m):,} 5-min bars with features")

    # Split
    is_5m  = df_5m[df_5m.index <= IS_END]
    oos_5m = df_5m[(df_5m.index >= OOS_START) & (df_5m.index <= OOS_END)]
    is_1m  = df_1m[df_1m.index <= IS_END]
    oos_1m = df_1m[(df_1m.index >= OOS_START) & (df_1m.index <= OOS_END)]

    sep("INTRADAY MEAN-REVERSION WITH DYNAMIC EXIT -- HYPOTHESIS 5")
    print(f"  Structural change: target/stop/timeout exit (not fixed EOD)")
    print(f"  Signal: RSI-{RSI_PERIOD} crossover on 5-min bars")
    print(f"  Exit: target (R:R) | stop ({STOP_ATR_MULT}x ATR-{ATR_PERIOD}) | timeout | EOD")
    print(f"  IS : {is_5m.index[0].date()} -> {is_5m.index[-1].date()}")
    print(f"  OOS: {oos_5m.index[0].date()} -> {oos_5m.index[-1].date()}")
    print(f"  Variants: {N_VARIANTS}  |  Bonferroni alpha: {BONFERRONI_P:.5f}"
          f"  |  Cost: {COST_PTS}pts/RT")

    # Diagnostic: how often does RSI reach extreme zones?
    sep("DIAGNOSTIC -- RSI signal frequency (full IS period)")
    for os_level in OVERSOLD_LEVELS:
        ob_level = 100 - os_level
        n_long  = ((is_5m["prev_rsi"] >= os_level) & (is_5m["rsi"] < os_level)).sum()
        n_short = ((is_5m["prev_rsi"] <= ob_level) & (is_5m["rsi"] > ob_level)).sum()
        n_days  = is_5m["date"].nunique()
        print(f"  RSI threshold {os_level}/{ob_level}: "
              f"{n_long} long signals, {n_short} short signals "
              f"({(n_long+n_short)/n_days:.1f}/day avg)")

    is_res, oos_res = [], []

    for os_level, rr, mh in product(OVERSOLD_LEVELS, RR_RATIOS, MAX_HOLDS):
        lbl = f"RSI={os_level}/{100-os_level}  R:R={rr:.1f}  hold<={mh}m"

        t_is  = run_variant(is_5m,  is_1m,  os_level, rr, mh)
        t_oos = run_variant(oos_5m, oos_1m, os_level, rr, mh)

        is_res.append( compute_metrics(t_is,  f"IS  | {lbl}"))
        oos_res.append(compute_metrics(t_oos, f"OOS | {lbl}"))

    sep("IN-SAMPLE RESULTS (2010 -- 2018)")
    for m in is_res:
        print_metrics(m)

    sep("OUT-OF-SAMPLE RESULTS (2019 -- 2024)")
    for m in oos_res:
        print_metrics(m)

    # Verdict
    sep("VERDICT")
    oos_pass = [m for m in oos_res if m.get("PASSED")]

    if oos_pass:
        print(f"\n  SIGNAL DETECTED -- {len(oos_pass)} variant(s) passed OOS:")
        for m in oos_pass:
            print(f"    -> {m['label']}")
        print("\n  DO NOT proceed to infrastructure yet.")
        print("  Next: robustness checks (walk-forward, regime decomp, bootstrap CIs).")
    else:
        best = max(oos_res, key=lambda m: m.get("mean_pnl", -999))
        print(f"\n  HYPOTHESIS REJECTED.")
        print(f"\n  Best OOS: {best['label']}")
        print(f"    mean={best.get('mean_pnl',0):+.4f}pts  "
              f"p={best.get('p_one_sided',1):.5f}  "
              f"PF={best.get('profit_factor',0):.3f}  "
              f"WR={best.get('win_rate',0):.1%}")
        if best.get("exit_reasons"):
            exit_str = "  ".join(f"{k}={v}" for k, v in
                                 sorted(best["exit_reasons"].items()))
            print(f"    Exits: {exit_str}")

        print(f"\n  STRUCTURAL INSIGHT: examine exit_reason distribution.")
        print(f"  If >60% are STOP exits, the mean-reversion thesis is wrong")
        print(f"  for ES at this timeframe. If >60% are TIMEOUT, the holding")
        print(f"  period is too short for the signal to resolve.")

    print(f"\n  Paste full output to research partner.")
    sep()
    print("  COMPLETE\n")


if __name__ == "__main__":
    main()