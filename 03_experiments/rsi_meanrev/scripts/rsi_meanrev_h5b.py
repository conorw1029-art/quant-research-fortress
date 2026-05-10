"""
HYPOTHESIS 5b: RSI Mean-Reversion -- MES Cost Model Retest
=============================================================
Amendment to H5. Single variant only. No parameter changes.

Rationale: H5 best variant (RSI=25/75, R:R=1.0, hold<=60m) showed
           PF=0.992, WR=53.4%, mean=-0.03pts OOS. Signal direction
           is correct; edge consumed by ES cost model (1.2pts/RT).
           MES has lower effective cost. Retest with MES costs.

Signal     : IDENTICAL to H5 (RSI-14 crossover, 25/75, 5-min bars)
Exit       : IDENTICAL to H5 (R:R=1.0, 1.5x ATR stop, 60m timeout)
Change     : Cost model only. ES 1.2pts -> MES 0.52pts/RT
             ($1.30 commission + 1 tick each side = 2 ticks × $1.25 = $2.50
              + $1.30 = $3.80 ... conservative: use $6.50 total = 0.52pts)

Variants   : 1 (no Bonferroni correction needed)
Alpha      : 0.05 (single test, one-sided)
Date       : 2026-04-24
Status     : PRE-REGISTERED. One retest. No further amendments.
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
# FROZEN PARAMETERS (identical to H5 best variant)
# -----------------------------------------------------------------
IS_END       = "2018-12-31"
OOS_START    = "2019-01-01"
OOS_END      = "2024-12-31"

ENTRY_START  = time(9, 45)
ENTRY_END    = time(14, 30)
FORCE_EXIT   = time(15, 55)

# THE ONLY CHANGE: cost model
ES_COST_PTS  = 1.2     # original (for comparison)
MES_COST_PTS = 0.52    # $6.50 / $12.50 per point = 0.52pts

RSI_PERIOD   = 14
ATR_PERIOD   = 14
STOP_ATR_MULT = 1.5
MAX_TRADES   = 3

# Single variant -- locked from H5 best
OVERSOLD     = 25
OVERBOUGHT   = 75
RR_RATIO     = 1.0
MAX_HOLD     = 60

# Pass/fail (single test, no Bonferroni)
ALPHA        = 0.05
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
# BACKTEST (single variant, dual cost model)
# -----------------------------------------------------------------

def run_backtest(df_5m, df_1m, cost_pts):
    """Run the single locked variant and return trades DataFrame."""
    signals = []
    for ts, row in df_5m.iterrows():
        bar_time = ts.time()
        if bar_time < ENTRY_START or bar_time > ENTRY_END:
            continue
        if np.isnan(row["rsi"]) or np.isnan(row["prev_rsi"]) or np.isnan(row["atr"]):
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

        pnl = (exit_price - entry_price) * direction - cost_pts
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

    # Annual breakdown
    trades_copy = trades.copy()
    trades_copy["year"] = pd.to_datetime(trades_copy["entry_time"]).dt.year
    annual = trades_copy.groupby("year")["pnl_pts"].agg(["mean", "count", "sum"])

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

    # Print annual breakdown
    annual = m.get("annual")
    if annual is not None and len(annual) > 0:
        print(f"    Annual breakdown:")
        for yr, row in annual.iterrows():
            print(f"      {yr}: n={int(row['count']):3d}  "
                  f"mean={row['mean']:+.3f}pts  "
                  f"total={row['sum']:+.1f}pts")


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

    is_5m  = df_5m[df_5m.index <= IS_END]
    oos_5m = df_5m[(df_5m.index >= OOS_START) & (df_5m.index <= OOS_END)]
    is_1m  = df_1m[df_1m.index <= IS_END]
    oos_1m = df_1m[(df_1m.index >= OOS_START) & (df_1m.index <= OOS_END)]

    sep("HYPOTHESIS 5b: RSI MEAN-REVERSION -- MES COST RETEST")
    print(f"  Single variant retest. Signal/exit identical to H5 best.")
    print(f"  ONLY CHANGE: cost model ES ({ES_COST_PTS}pts) -> MES ({MES_COST_PTS}pts)")
    print(f"  Variant: RSI={OVERSOLD}/{OVERBOUGHT}  R:R={RR_RATIO}  hold<={MAX_HOLD}m")
    print(f"  Alpha: {ALPHA} (single test, no Bonferroni)")
    print(f"  IS : {is_5m.index[0].date()} -> {is_5m.index[-1].date()}")
    print(f"  OOS: {oos_5m.index[0].date()} -> {oos_5m.index[-1].date()}")

    # Run both cost models for comparison
    sep("IN-SAMPLE RESULTS (2010 -- 2018)")

    is_trades = run_backtest(is_5m, is_1m, cost_pts=0)  # gross trades (no cost)
    # Apply costs after for both models
    is_es  = is_trades.copy(); is_es["pnl_pts"]  = is_es["pnl_pts"] - ES_COST_PTS
    is_mes = is_trades.copy(); is_mes["pnl_pts"] = is_mes["pnl_pts"] - MES_COST_PTS

    # Wait -- the run_backtest already subtracts cost. Re-run properly.
    is_trades_es  = run_backtest(is_5m, is_1m, ES_COST_PTS)
    is_trades_mes = run_backtest(is_5m, is_1m, MES_COST_PTS)

    m_is_es  = compute_metrics(is_trades_es,  "IS  | ES  cost (1.20pts)", ALPHA)
    m_is_mes = compute_metrics(is_trades_mes, "IS  | MES cost (0.52pts)", ALPHA)
    print_metrics(m_is_es)
    print_metrics(m_is_mes)

    sep("OUT-OF-SAMPLE RESULTS (2019 -- 2024)")

    oos_trades_es  = run_backtest(oos_5m, oos_1m, ES_COST_PTS)
    oos_trades_mes = run_backtest(oos_5m, oos_1m, MES_COST_PTS)

    m_oos_es  = compute_metrics(oos_trades_es,  "OOS | ES  cost (1.20pts)", ALPHA)
    m_oos_mes = compute_metrics(oos_trades_mes, "OOS | MES cost (0.52pts)", ALPHA)
    print_metrics(m_oos_es)
    print_metrics(m_oos_mes)

    # Gross stats (zero cost) for reference
    sep("REFERENCE: GROSS P&L (zero cost)")
    oos_trades_gross = run_backtest(oos_5m, oos_1m, 0.0)
    m_gross = compute_metrics(oos_trades_gross, "OOS | GROSS (0 cost)", ALPHA)
    print_metrics(m_gross)

    # Verdict
    sep("VERDICT")
    if m_oos_mes["PASSED"]:
        print(f"\n  PASS -- MES cost model produces viable edge.")
        print(f"  OOS mean: {m_oos_mes['mean_pnl']:+.4f}pts/trade")
        print(f"  OOS PF:   {m_oos_mes['profit_factor']:.3f}")
        print(f"  OOS WR:   {m_oos_mes['win_rate']:.1%}")
        print(f"\n  CRITICAL: This is a FEASIBILITY pass, not a deployment signal.")
        print(f"  NEXT REQUIRED STEPS (do NOT skip):")
        print(f"    1. Walk-forward validation (rolling IS/OOS windows)")
        print(f"    2. Bootstrap confidence intervals on mean PnL")
        print(f"    3. Regime decomposition (vol buckets: VIX<15, 15-25, >25)")
        print(f"    4. Monte Carlo permutation test (is this better than random?)")
        print(f"    5. Paper trade for minimum 4 weeks before any real capital")
        print(f"\n  Paste full output to research partner for next steps.")
    else:
        print(f"\n  FAIL -- Even MES costs destroy the edge.")
        print(f"  OOS mean: {m_oos_mes['mean_pnl']:+.4f}pts/trade")
        print(f"\n  Gross edge: {m_gross['mean_pnl']:+.4f}pts/trade")
        print(f"  ES cost:    {ES_COST_PTS}pts  ->  net {m_oos_es['mean_pnl']:+.4f}pts")
        print(f"  MES cost:   {MES_COST_PTS}pts  ->  net {m_oos_mes['mean_pnl']:+.4f}pts")
        print(f"\n  The gross edge is too small to survive ANY realistic cost model.")
        print(f"  RSI mean-reversion on ES/MES 5-min is not viable.")
        print(f"\n  RECOMMENDATION: Move to Hypothesis 6.")
        print(f"  Five rejections across gap/breakout/VWAP/momentum/RSI confirms:")
        print(f"  single-indicator strategies cannot overcome ES transaction costs.")
        print(f"  Next direction: test on a DIFFERENT INSTRUMENT (NQ, CL, GC)")
        print(f"  or move to calendar/structural effects (turn-of-month, FOMC drift).")

    sep()
    print("  COMPLETE\n")


if __name__ == "__main__":
    main()