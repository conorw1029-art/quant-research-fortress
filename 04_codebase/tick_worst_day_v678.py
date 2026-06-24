#!/usr/bin/env python3
"""
tick_worst_day_v678.py — Compute worst single-day P&L for top V678 survivors
=============================================================================
Loads the V678 WFO results JSON, picks the top new survivors not already in
the allowlist, runs each on the FULL available history with its best_params,
and computes worst-calendar-day P&L (full contract) and micro equivalent (/10).

Output: worst_day table printed + saved to 05_backtests/worst_day_v678.json
Usage:  python tick_worst_day_v678.py
"""
from __future__ import annotations
import json, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")

ROOT          = Path(__file__).parent.parent
_LOCAL_BAR    = ROOT / "01_data" / "tick_bars"
_VPS_BAR      = Path("/opt/fortress/01_data/tick_bars")
BAR_DIR       = _LOCAL_BAR if _LOCAL_BAR.exists() and any(_LOCAL_BAR.glob("*.parquet")) else _VPS_BAR
OUT_DIR       = ROOT / "05_backtests"; OUT_DIR.mkdir(exist_ok=True)
RESULTS_JSON  = OUT_DIR / "tick_results_v678_20260624_1306.json"

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import SPECS, run_backtest
from tick_strategies_v6 import STRATEGIES_V6
from tick_strategies_v7 import STRATEGIES_V7
from tick_strategies_v8 import STRATEGIES_V8

STOP_MULT = 1.5
TP_MULT   = 3.0

# Strategies already in the allowlist — skip these
ALREADY_IN_ALLOWLIST = {
    "GC/vwap_mean_reversion/30m", "GC/pivot_reversal/30m",
    "SI/opening_range_fakeout/30m", "SI/consecutive_close_momentum/3m",
    "GC/pivot_reversal/15m", "SI/ema_crossover/1m",
    "SI/vwap_mean_reversion/15m", "SI/opening_range_fakeout/3m",
    "GC/donchian_breakout/15m", "SI/consecutive_close_momentum/5m",
    "SI/ema_crossover/30m", "GC/consecutive_close_momentum/15m",
    "SI/ma_slope_regime/30m", "SI/ema_crossover/5m",
    "SI/consecutive_close_momentum/15m", "SI/consecutive_close_momentum/1m",
    "GC/close_position_momentum/15m", "ES/overnight_gap_fill/30m",
    "ES/overnight_gap_fill/15m", "NQ/ma_slope_regime/30m",
    "NQ/inside_bar_breakout/15m", "NQ/vwap_mean_reversion/30m",
    "ES/vwap_mean_reversion/30m",
}

ALL_STRATS = {s["name"]: s for s in STRATEGIES_V6 + STRATEGIES_V7 + STRATEGIES_V8}


def load_bars(sym: str, bar_min: int) -> pd.DataFrame | None:
    p = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def worst_day(label: str, best_params: dict) -> dict | None:
    parts = label.split("/")
    if len(parts) != 3:
        return None
    sym, strat_name, bar_str = parts
    bar_min = int(bar_str.rstrip("m"))

    df = load_bars(sym, bar_min)
    if df is None or len(df) < 50:
        return None

    strat = ALL_STRATS.get(strat_name)
    if strat is None:
        return None

    fn = strat["compute"]
    try:
        sig = fn(df, **best_params)
        tr  = run_backtest(df, sig, sym, stop_atr_mult=STOP_MULT, tp_atr_mult=TP_MULT)
    except Exception as e:
        return {"error": str(e)}

    if tr.empty or len(tr) < 5:
        return None

    spec = SPECS.get(sym, SPECS["GC"])
    pv   = spec["point_value"]

    # Group by calendar date
    if "entry_time" in tr.columns:
        dates = pd.to_datetime(tr["entry_time"], utc=True).dt.date
    else:
        dates = tr.index.date

    daily = tr["dollar_pnl"].groupby(dates).sum()
    worst = float(daily.min())
    worst_micro = worst / 10.0  # micro is 1/10th of full contract

    n_days_neg = int((daily < 0).sum())
    n_days     = int(len(daily))
    win_rate   = float((tr["dollar_pnl"] > 0).mean())

    return {
        "label":         label,
        "worst_day_usd": round(worst, 2),
        "worst_micro":   round(worst_micro, 2),
        "n_trades":      len(tr),
        "total_pnl":     round(float(tr["dollar_pnl"].sum()), 2),
        "win_rate":      round(win_rate, 3),
        "n_days":        n_days,
        "n_days_neg":    n_days_neg,
        "data_bars":     len(df),
        "note":          "full history backtest with WFO best_params",
    }


def main():
    with open(RESULTS_JSON) as f:
        v678 = json.load(f)

    # Filter to NEW survivors above DSR=1.5 with n_trades >= 50
    survivors = [
        r for r in v678
        if isinstance(r.get("dsr"), (int, float))
        and r["dsr"] >= 1.5
        and r.get("n_trades", 0) >= 50
        and r.get("label", "") not in ALREADY_IN_ALLOWLIST
    ]
    survivors.sort(key=lambda x: -x["dsr"])

    print(f"\n{'='*70}")
    print(f"  Worst-Day Analysis for {len(survivors)} NEW V678 survivors (DSR>=1.5, n>=50)")
    print(f"  Data dir: {BAR_DIR}")
    print(f"{'='*70}\n")
    print(f"  {'Label':<48}  {'WorstDay':>10}  {'WorstMicro':>11}  {'WR':>6}  {'n':>5}  {'Safe?':>6}")
    print(f"  {'-'*48}  {'-'*10}  {'-'*11}  {'-'*6}  {'-'*5}  {'-'*6}")

    all_results = []
    for r in survivors:
        label       = r["label"]
        best_params = r.get("best_params", {})
        wd = worst_day(label, best_params)
        if wd is None or "error" in wd:
            err = wd.get("error", "skip") if wd else "skip"
            print(f"  {label:<48}  {'N/A':>10}  {'N/A':>11}  {'?':>6}  {'?':>5}  {'?':>6}  [{err[:30]}]")
            all_results.append({"label": label, "dsr": r["dsr"], "skip": err})
            continue

        wd["dsr"]        = r["dsr"]
        wd["oos_sharpe"] = r.get("oos_sharpe")
        wd["best_params"] = best_params
        safe = "YES" if wd["worst_micro"] >= -1000 else "NO"
        all_results.append(wd)

        print(f"  {label:<48}  "
              f"${wd['worst_day_usd']:>9,.0f}  "
              f"${wd['worst_micro']:>10,.0f}  "
              f"{wd['win_rate']:>5.1%}  "
              f"{wd['n_trades']:>5d}  "
              f"{'✓' if safe=='YES' else '✗':>6}")

    out = OUT_DIR / "worst_day_v678.json"
    with open(out, "w") as f:
        json.dump(all_results, f, indent=2)

    print(f"\n  Results → {out}")

    safe   = [r for r in all_results if isinstance(r.get("worst_micro"), float) and r["worst_micro"] >= -1000]
    unsafe = [r for r in all_results if isinstance(r.get("worst_micro"), float) and r["worst_micro"] < -1000]
    print(f"\n  SUMMARY: {len(safe)} safe for dry-run (worst_micro >= -$1,000)")
    if safe:
        print(f"\n  === SAFE — ready for REVIEW_REQUIRED status ===")
        for r in sorted(safe, key=lambda x: -x.get("dsr", 0)):
            print(f"    DSR={r['dsr']:.2f}  worst_micro=${r['worst_micro']:,.0f}  {r['label']}")
    if unsafe:
        print(f"\n  === UNSAFE — worst_micro < -$1,000 (re-enable at $5k+ equity) ===")
        for r in sorted(unsafe, key=lambda x: x.get("worst_micro", 0)):
            print(f"    DSR={r['dsr']:.2f}  worst_micro=${r['worst_micro']:,.0f}  {r['label']}")


if __name__ == "__main__":
    main()
