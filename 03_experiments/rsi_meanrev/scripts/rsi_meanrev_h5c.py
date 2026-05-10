"""
HYPOTHESIS 5c: RSI Mean-Reversion with Regime Filter (MES)
=============================================================
Amendment to H5b. Adds trend-regime gate to skip trades in
trending markets where mean-reversion underperforms.

Diagnosis from H5b: strategy profits in volatile/choppy regimes
(2020, 2022) and loses in trending/low-vol regimes (2021, 2023).
A simple trend filter applied BEFORE entry should isolate the
favourable regime.

Signal     : IDENTICAL to H5/H5b (RSI-14 crossover 25/75, 5-min bars)
Exit       : IDENTICAL (R:R=1.0, 1.5x ATR-14 stop, 60m timeout)
Cost       : MES 0.52pts/RT
NEW FILTER : Daily trend slope. Compute 20-day SMA of daily close.
             slope = (SMA_today - SMA_yesterday). If |slope| > threshold,
             market is trending -> skip all trades that day.
             This is causal: uses prior day's close, available before RTH.
Variants   : slope_threshold in {0.5, 1.0, 1.5} pts/day = 3 variants
Bonferroni : 0.10 / 3 = 0.0333
Alpha      : 0.0333
Date       : 2026-04-24
Status     : PRE-REGISTERED. 3 variants. No post-hoc additions.
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
# FROZEN PARAMETERS
# -----------------------------------------------------------------
IS_END       = "2018-12-31"
OOS_START    = "2019-01-01"
OOS_END      = "2024-12-31"

ENTRY_START  = time(9, 45)
ENTRY_END    = time(14, 30)
FORCE_EXIT   = time(15, 55)

MES_COST_PTS = 0.52
RSI_PERIOD   = 14
ATR_PERIOD   = 14
STOP_ATR_MULT = 1.5
MAX_TRADES   = 3

OVERSOLD     = 25
OVERBOUGHT   = 75
RR_RATIO     = 1.0
MAX_HOLD     = 60

# Regime filter
SMA_PERIOD   = 20              # days
SLOPE_THRESHOLDS = [0.5, 1.0, 1.5]  # pts/day

N_VARIANTS   = len(SLOPE_THRESHOLDS)
BONFERRONI_P = 0.10 / N_VARIANTS  # 0.0333
MIN_PF       = 1.25
MIN_WR       = 0.40
MAX_DD_STOPS = 20.0


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
    t = df.index.time
    df = df[(t >= time(9, 30)) & (t <= time(15, 59))]
    print(f"  {len(df):,} 1-min RTH bars  {df.index[0].date()} -> {df.index[-1].date()}")
    return df


def resample_5min(df):
    bars = df.resample("5min").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()
    t = bars.index.time
    return bars[(t >= time(9, 30)) & (t <= time(15, 55))]


# -----------------------------------------------------------------
# DAILY REGIME CLASSIFICATION (causal)
# -----------------------------------------------------------------

def build_daily_regime(df_1m: pd.DataFrame) -> pd.DataFrame:
    """
    Build a daily table with SMA slope for regime classification.
    Uses PRIOR day's close to compute today's regime -- strictly causal.
    """
    # Get daily close (last bar of each day)
    daily = df_1m.groupby(df_1m.index.date).agg(
        daily_close=("close", "last"),
        daily_high=("high", "max"),
        daily_low=("low", "min"),
    )
    daily.index = pd.to_datetime(daily.index)

    # SMA of daily close
    daily["sma20"] = daily["daily_close"].rolling(SMA_PERIOD, min_periods=SMA_PERIOD).mean()

    # Slope = change in SMA (pts per day)
    daily["sma_slope"] = daily["sma20"].diff()

    # Shift by 1 so today's regime is based on YESTERDAY's slope
    # (available before today's RTH open)
    daily["regime_slope"] = daily["sma_slope"].shift(1)

    return daily[["regime_slope"]].dropna()


# -----------------------------------------------------------------
# FEATURES
# -----------------------------------------------------------------

def compute_rsi(close, period):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.clip(lower=1e-10)
    return 100 - (100 / (1 + rs))


def add_features(df):
    df = df.copy()
    df["date"] = df.index.date
    df["rsi"] = compute_rsi(df["close"], RSI_PERIOD)
    df["prev_rsi"] = df["rsi"].shift(1)
    prev_close = df["close"].shift(1)
    tr = np.maximum(df["high"] - df["low"],
         np.maximum(abs(df["high"] - prev_close),
                    abs(df["low"] - prev_close)))
    df["atr"] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    return df.dropna()


# -----------------------------------------------------------------
# BACKTEST
# -----------------------------------------------------------------

def run_backtest(df_5m, df_1m, regime_df, slope_threshold):
    """
    Identical to H5b but skips trades on days where
    |regime_slope| > slope_threshold.
    """
    # Build set of tradeable dates
    tradeable = regime_df[abs(regime_df["regime_slope"]) <= slope_threshold].index
    tradeable_dates = set(tradeable.date)

    signals = []
    for ts, row in df_5m.iterrows():
        bar_time = ts.time()
        if bar_time < ENTRY_START or bar_time > ENTRY_END:
            continue
        if np.isnan(row["rsi"]) or np.isnan(row["prev_rsi"]) or np.isnan(row["atr"]):
            continue

        # Regime gate: skip if today is trending
        if ts.date() not in tradeable_dates:
            continue

        if row["prev_rsi"] >= OVERSOLD and row["rsi"] < OVERSOLD:
            signals.append((ts, 1, row["close"], row["atr"]))
        elif row["prev_rsi"] <= OVERBOUGHT and row["rsi"] > OVERBOUGHT:
            signals.append((ts, -1, row["close"], row["atr"]))

    trades = []
    used_dates = {}

    for sig_ts, direction, entry_price, atr in signals:
        sig_date = sig_ts.date()
        if used_dates.get(sig_date, 0) >= MAX_TRADES:
            continue

        stop_dist = STOP_ATR_MULT * atr
        stop_price = entry_price - direction * stop_dist
        target_dist = RR_RATIO * stop_dist
        target_price = entry_price + direction * target_dist
        timeout_ts = sig_ts + pd.Timedelta(minutes=MAX_HOLD)

        exit_slice = df_1m[df_1m.index > sig_ts]
        exit_price = exit_time = exit_reason = None

        for bar_ts, bar in exit_slice.iterrows():
            if bar_ts.time() >= FORCE_EXIT:
                exit_price, exit_time, exit_reason = bar["close"], bar_ts, "EOD"
                break
            if direction == 1 and bar["low"] <= stop_price:
                exit_price, exit_time, exit_reason = stop_price, bar_ts, "STOP"
                break
            if direction == -1 and bar["high"] >= stop_price:
                exit_price, exit_time, exit_reason = stop_price, bar_ts, "STOP"
                break
            if direction == 1 and bar["high"] >= target_price:
                exit_price, exit_time, exit_reason = target_price, bar_ts, "TARGET"
                break
            if direction == -1 and bar["low"] <= target_price:
                exit_price, exit_time, exit_reason = target_price, bar_ts, "TARGET"
                break
            if bar_ts >= timeout_ts:
                exit_price, exit_time, exit_reason = bar["close"], bar_ts, "TIMEOUT"
                break
            if bar_ts.date() != sig_date:
                exit_price, exit_time, exit_reason = bar["open"], bar_ts, "NEXTDAY"
                break

        if exit_price is None:
            continue

        pnl = (exit_price - entry_price) * direction - MES_COST_PTS
        used_dates[sig_date] = used_dates.get(sig_date, 0) + 1

        trades.append({
            "entry_time": sig_ts, "exit_time": exit_time,
            "entry_price": entry_price, "exit_price": exit_price,
            "direction": direction, "pnl_pts": pnl,
            "stop_dist": stop_dist, "exit_reason": exit_reason,
        })

    return pd.DataFrame(trades) if trades else pd.DataFrame()


# -----------------------------------------------------------------
# METRICS
# -----------------------------------------------------------------

def compute_metrics(trades, label, alpha):
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

    stop_trades = trades[trades["exit_reason"] == "STOP"]
    avg_stop = abs((stop_trades["pnl_pts"] + MES_COST_PTS)).mean() \
               if len(stop_trades) > 0 else max(abs(pnl).mean(), 0.01)
    avg_stop = max(avg_stop, 0.01)
    dd_ratio = max_dd / avg_stop

    mid = n // 2
    def sharpe(x):
        if len(x) < 2: return 0.0
        return x.mean() / (x.std() + 1e-9) * np.sqrt(252)
    sh1 = sharpe(pnl[:mid]); sh2 = sharpe(pnl[mid:])

    trades_c = trades.copy()
    trades_c["year"] = pd.to_datetime(trades_c["entry_time"]).dt.year
    annual = trades_c.groupby("year")["pnl_pts"].agg(["mean", "count", "sum"])

    reasons = trades["exit_reason"].value_counts().to_dict()

    criteria = {
        "mean_pnl_positive": mean_pnl > 0,
        "p_value":           p_one < alpha,
        "profit_factor":     pf >= MIN_PF,
        "win_rate":          win_rate >= MIN_WR,
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
        "annual": annual,
        "criteria": criteria,
        "PASSED": passed,
        "status": "PASS" if passed else "FAIL",
    }


def print_metrics(m):
    if m.get("status") == "INSUFFICIENT DATA":
        print(f"  {m['label']}: INSUFFICIENT DATA ({m['n_trades']})")
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
    print(f"    Exits: {'  '.join(f'{k}={v}' for k, v in sorted(exits.items()))}")
    fails = [k for k, v in m["criteria"].items() if not v]
    if fails:
        print(f"    FAILED: {', '.join(fails)}")
    annual = m.get("annual")
    if annual is not None and len(annual) > 0:
        print(f"    Annual:")
        for yr, row in annual.iterrows():
            print(f"      {yr}: n={int(row['count']):3d}  "
                  f"mean={row['mean']:+.3f}pts  total={row['sum']:+.1f}pts")


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

    print("Building daily regime table ...")
    regime_df = build_daily_regime(df_1m)
    print(f"  {len(regime_df)} days with regime classification")

    print("Resampling to 5-min bars ...")
    df_5m = resample_5min(df_1m)
    df_5m = add_features(df_5m)
    print(f"  {len(df_5m):,} 5-min bars with features")

    is_5m  = df_5m[df_5m.index <= IS_END]
    oos_5m = df_5m[(df_5m.index >= OOS_START) & (df_5m.index <= OOS_END)]
    is_1m  = df_1m[df_1m.index <= IS_END]
    oos_1m = df_1m[(df_1m.index >= OOS_START) & (df_1m.index <= OOS_END)]

    sep("HYPOTHESIS 5c: RSI MEAN-REVERSION + REGIME FILTER (MES)")
    print(f"  Base signal: RSI={OVERSOLD}/{OVERBOUGHT}  R:R={RR_RATIO}  hold<={MAX_HOLD}m")
    print(f"  NEW: regime gate = |SMA-{SMA_PERIOD} daily slope| <= threshold")
    print(f"  Cost: MES {MES_COST_PTS}pts/RT")
    print(f"  Variants: {N_VARIANTS}  |  Bonferroni alpha: {BONFERRONI_P:.4f}")
    print(f"  IS : {is_5m.index[0].date()} -> {is_5m.index[-1].date()}")
    print(f"  OOS: {oos_5m.index[0].date()} -> {oos_5m.index[-1].date()}")

    # Diagnostic: how many days filtered at each threshold
    sep("DIAGNOSTIC -- Regime filter impact on trade days")
    for st in SLOPE_THRESHOLDS:
        is_regime = regime_df[regime_df.index <= IS_END]
        oos_regime = regime_df[(regime_df.index >= OOS_START) & (regime_df.index <= OOS_END)]
        is_ok  = (abs(is_regime["regime_slope"]) <= st).mean()
        oos_ok = (abs(oos_regime["regime_slope"]) <= st).mean()
        print(f"  slope <= {st:.1f}: IS {is_ok:.1%} days tradeable, "
              f"OOS {oos_ok:.1%} days tradeable")

    # H5b baseline (no filter) for comparison
    sep("BASELINE (H5b -- no regime filter, MES cost)")
    t_is_base  = run_backtest(is_5m, is_1m, regime_df, slope_threshold=999)
    t_oos_base = run_backtest(oos_5m, oos_1m, regime_df, slope_threshold=999)
    m_is_base  = compute_metrics(t_is_base,  "IS  | no filter (baseline)", BONFERRONI_P)
    m_oos_base = compute_metrics(t_oos_base, "OOS | no filter (baseline)", BONFERRONI_P)
    print_metrics(m_is_base)
    print_metrics(m_oos_base)

    # Run variants
    is_res, oos_res = [], []
    for st in SLOPE_THRESHOLDS:
        lbl = f"slope<={st:.1f}pts/day"
        t_is  = run_backtest(is_5m, is_1m, regime_df, st)
        t_oos = run_backtest(oos_5m, oos_1m, regime_df, st)
        is_res.append( compute_metrics(t_is,  f"IS  | {lbl}", BONFERRONI_P))
        oos_res.append(compute_metrics(t_oos, f"OOS | {lbl}", BONFERRONI_P))

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
        print(f"\n  SIGNAL DETECTED -- {len(oos_pass)} variant(s) passed ALL criteria:")
        for m in oos_pass:
            print(f"    -> {m['label']}")
            print(f"       mean={m['mean_pnl']:+.4f}pts  WR={m['win_rate']:.1%}  "
                  f"PF={m['profit_factor']:.3f}  p={m['p_one_sided']:.5f}")
        print(f"\n  THIS IS A FEASIBILITY PASS. Not a deployment signal.")
        print(f"  MANDATORY next steps (do NOT skip ANY):")
        print(f"    1. Walk-forward validation (3yr IS / 1yr OOS rolling)")
        print(f"    2. Bootstrap 95% CI on mean PnL (10,000 resamples)")
        print(f"    3. Monte Carlo permutation test (10,000 shuffles)")
        print(f"    4. Regime decomposition: VIX<15 / 15-25 / >25")
        print(f"    5. Check: does the filter actually predict losers,")
        print(f"       or does it just reduce sample size?")
        print(f"    6. Paper trade minimum 4 weeks on MES")
        print(f"\n  DO NOT build infrastructure. DO NOT open a prop firm account.")
        print(f"  Paste full output to research partner.")
    else:
        # Find best
        best = max(oos_res, key=lambda m: m.get("mean_pnl", -999))
        print(f"\n  HYPOTHESIS REJECTED.")
        print(f"\n  Best OOS: {best['label']}")
        print(f"    mean={best.get('mean_pnl',0):+.4f}pts  "
              f"p={best.get('p_one_sided',1):.5f}  "
              f"PF={best.get('profit_factor',0):.3f}")

        # Compare vs baseline
        print(f"\n  Baseline (no filter): mean={m_oos_base.get('mean_pnl',0):+.4f}pts  "
              f"PF={m_oos_base.get('profit_factor',0):.3f}")
        print(f"\n  Did the filter help?")
        if best.get("mean_pnl", -999) > m_oos_base.get("mean_pnl", -999):
            print(f"    Yes -- filter improved mean PnL but not enough to pass.")
            print(f"    Edge exists but is too thin for MES costs.")
        else:
            print(f"    No -- filter did not improve results.")
            print(f"    The regime thesis is wrong or the SMA slope is")
            print(f"    the wrong regime indicator.")

        print(f"\n  RECOMMENDATION: This line of research (RSI mean-reversion)")
        print(f"  has been thoroughly explored across H5/5b/5c.")
        print(f"  Move to Hypothesis 6: calendar/structural effect")
        print(f"  (turn-of-month, FOMC drift) -- different mechanism entirely.")

    sep()
    print("  COMPLETE\n")


if __name__ == "__main__":
    main()