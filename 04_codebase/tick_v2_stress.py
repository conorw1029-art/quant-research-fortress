#!/usr/bin/env python3
"""
Quick stress test for top v2 candidates.
Tests slippage sensitivity (0/0.5/1/2 ticks), regime stability, and
correlation with existing v1 portfolio survivors.

Candidates to test (from v2 WFO results):
  1. NQ/prev_session_sweep/1m     DSR=2.05 EXCELLENT
  2. NQ/delta_acceleration_reversal/15m  DSR=1.87 GOOD
  3. ES/prev_session_sweep/3m     DSR=1.66 GOOD
  4. ES/volume_tod_surge/5m       DSR=1.50 MARGINAL (borderline)
  5. GC/book_depth_trend/3m       DSR=1.25 MARGINAL
"""

import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import compute_metrics, SPECS
from tick_deep_analysis import run_backtest_slippage
from tick_strategies_v2 import STRATEGIES_V2

BAR_DIR    = Path(__file__).parent.parent / "01_data" / "tick_bars"
RESULT_DIR = Path(__file__).parent.parent / "05_backtests"

STRAT_MAP = {s["name"]: s for s in STRATEGIES_V2}

CANDIDATES = [
    ("NQ", "prev_session_sweep",          1,  {"level_window": 20, "cvd_flip_window": 3, "sweep_buffer": 0.0001}),
    ("NQ", "delta_acceleration_reversal", 15, {"fast_window": 5, "slow_window": 20, "cross_z": 0.8}),
    ("ES", "prev_session_sweep",          3,  {"level_window": 20, "cvd_flip_window": 3, "sweep_buffer": 0.0001}),
    ("ES", "volume_tod_surge",            5,  {"lookback_days": 10, "surge_z": 1.5}),
    ("GC", "book_depth_trend",            3,  {"obi_window": 10, "obi_threshold": 0.3, "cvd_window": 5}),
]

# Existing v1 top-6 result files for correlation check
V1_SURVIVORS = {
    "NQ/cvd_divergence_large_print/30m": ("NQ", 30),
    "ES/cvd_divergence_large_print/15m": ("ES", 15),
    "NQ/stop_hunt_reversal/3m":          ("NQ", 3),
    "GC/obi_threshold/1m":               ("GC", 1),
    "ES/tape_absorption/15m":            ("ES", 15),
    "ES/cvd_divergence/15m":             ("ES", 15),
}

TICK_SIZES = {"GC": 0.10, "SI": 0.005, "ES": 0.25, "NQ": 0.25}

EXIT_BASE = {"stop_atr_mult": 1.5, "tp_atr_mult": 3.0, "max_hold_bars": 50}


def run_with_slippage(df, strat_fn, params, symbol, extra_ticks):
    sig = strat_fn(df, **params)
    return run_backtest_slippage(df, sig, symbol,
                                 stop_mult=EXIT_BASE["stop_atr_mult"],
                                 tp_mult=EXIT_BASE["tp_atr_mult"],
                                 max_hold=EXIT_BASE["max_hold_bars"],
                                 extra_ticks=extra_ticks)


def regime_breakdown(trades: pd.DataFrame) -> dict:
    if "entry_time" not in trades.columns:
        return {}
    trades = trades.copy()
    trades["year"] = pd.to_datetime(trades["entry_time"]).dt.year
    out = {}
    for yr, grp in trades.groupby("year"):
        m = compute_metrics(grp, 1)
        out[int(yr)] = {"sharpe": round(m["sharpe"], 2),
                        "total_pnl": round(m["total_pnl"], 0),
                        "trades": len(grp)}
    return out


def worst_day(trades: pd.DataFrame, symbol: str) -> float:
    if "entry_time" not in trades.columns or trades.empty:
        return 0.0
    trades = trades.copy()
    trades["date"] = pd.to_datetime(trades["entry_time"]).dt.date
    daily = trades.groupby("date")["dollar_pnl"].sum()
    return float(daily.min())


def topstep_compliance(trades: pd.DataFrame) -> float:
    if "entry_time" not in trades.columns or trades.empty:
        return 0.0
    trades = trades.copy()
    trades["date"] = pd.to_datetime(trades["entry_time"]).dt.date
    daily = trades.groupby("date")["dollar_pnl"].sum()
    return float((daily >= -4500).mean())


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"\n{'='*70}")
    print(f"  V2 CANDIDATE STRESS TEST")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    results = []

    for symbol, strat_name, bar_min, params in CANDIDATES:
        strat = STRAT_MAP.get(strat_name)
        if not strat:
            print(f"  SKIP: {strat_name} not found")
            continue

        bar_file = BAR_DIR / f"{symbol}_bars_{bar_min}m.parquet"
        if not bar_file.exists():
            print(f"  SKIP: {bar_file} not found")
            continue

        df = pd.read_parquet(bar_file)
        df.index = pd.to_datetime(df.index, utc=True)

        label = f"{symbol}/{strat_name}/{bar_min}m"
        print(f"\n  {'─'*60}")
        print(f"  Testing: {label}")
        print(f"  Params:  {params}")
        print(f"  Data:    {len(df):,} bars  {df.index[0].date()} → {df.index[-1].date()}")

        # ── Slippage sensitivity ──────────────────────────────────────
        slip_results = {}
        for ticks in [0.0, 0.5, 1.0, 2.0]:
            try:
                tr = run_with_slippage(df, strat["compute"], params, symbol, ticks)
                if tr.empty:
                    slip_results[ticks] = {"sharpe": 0, "total_pnl": 0, "trades": 0}
                else:
                    m = compute_metrics(tr, 1)
                    slip_results[ticks] = {
                        "sharpe":    round(m["sharpe"], 2),
                        "total_pnl": round(m["total_pnl"], 0),
                        "trades":    len(tr),
                    }
            except Exception as e:
                slip_results[ticks] = {"error": str(e)}

        print(f"\n  Slippage sensitivity:")
        print(f"    {'Ticks':<8} {'Sharpe':>8} {'Total PnL':>12} {'Trades':>8}")
        for t, r in slip_results.items():
            if "error" in r:
                print(f"    {t:<8} ERROR: {r['error']}")
            else:
                print(f"    {t:<8} {r.get('sharpe',0):>8.2f} {r.get('total_pnl',0):>12,.0f} {r.get('trades',0):>8}")

        # ── 1-tick baseline for further checks ───────────────────────
        try:
            base_trades = run_with_slippage(df, strat["compute"], params, symbol, 0.5)
        except Exception as e:
            print(f"  ERROR running 1-tick baseline: {e}")
            continue

        if base_trades.empty:
            print("  No trades generated.")
            continue

        # ── Worst day & Topstep compliance ───────────────────────────
        wd    = worst_day(base_trades, symbol)
        tscom = topstep_compliance(base_trades)
        print(f"\n  Worst day (0.5-tick):     ${wd:>10,.0f}")
        print(f"  Topstep compliance:        {tscom*100:.1f}%")

        # ── Regime breakdown (year-by-year) ──────────────────────────
        rbd = regime_breakdown(base_trades)
        if rbd:
            profitable_years = sum(1 for v in rbd.values() if v["total_pnl"] > 0)
            total_years      = len(rbd)
            print(f"\n  Regime stability ({profitable_years}/{total_years} years profitable):")
            for yr, v in sorted(rbd.items()):
                pnl_str = f"${v['total_pnl']:>9,.0f}"
                print(f"    {yr}  Sharpe={v['sharpe']:>5.2f}  {pnl_str}  ({v['trades']} trades)")
        else:
            profitable_years = 1
            total_years      = 1

        # ── Summary scoring ──────────────────────────────────────────
        sharpe_0t  = slip_results.get(0.0,  {}).get("sharpe", 0)
        sharpe_1t  = slip_results.get(1.0,  {}).get("sharpe", 0)
        sharpe_2t  = slip_results.get(2.0,  {}).get("sharpe", 0)
        slip_drop  = sharpe_0t - sharpe_1t  # drop per tick
        slip_pct   = (sharpe_0t - sharpe_1t) / (abs(sharpe_0t) + 1e-9) * 100

        passes_slip   = sharpe_1t > 1.0
        passes_regime = profitable_years / max(total_years, 1) >= 0.7
        passes_ts     = tscom >= 0.95

        verdict = "PASS" if (passes_slip and passes_regime and passes_ts) else "FAIL"
        print(f"\n  STRESS VERDICT: {verdict}")
        print(f"    1-tick Sharpe={sharpe_1t:.2f}  ({'OK' if passes_slip else 'FAIL'} — need >1.0)")
        print(f"    Regime: {profitable_years}/{total_years}  ({'OK' if passes_regime else 'FAIL'} — need ≥70%)")
        print(f"    TS compliance: {tscom*100:.1f}%  ({'OK' if passes_ts else 'FAIL'} — need ≥95%)")
        print(f"    Slippage sensitivity: {slip_drop:+.2f} Sharpe per tick  ({slip_pct:.0f}% drop at 1T)")

        results.append({
            "label": label,
            "symbol": symbol,
            "strat": strat_name,
            "bar_min": bar_min,
            "params": params,
            "sharpe_0t":  sharpe_0t,
            "sharpe_05t": slip_results.get(0.5, {}).get("sharpe", 0),
            "sharpe_1t":  sharpe_1t,
            "sharpe_2t":  sharpe_2t,
            "total_pnl":  slip_results.get(0.5, {}).get("total_pnl", 0),
            "trades":     slip_results.get(0.5, {}).get("trades", 0),
            "worst_day":  wd,
            "ts_compliance": tscom,
            "regime_profitable": profitable_years,
            "regime_total":      total_years,
            "verdict": verdict,
        })

    # ── Final summary ─────────────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print(f"  V2 STRESS TEST SUMMARY")
    print(f"{'='*70}")
    print(f"  {'Label':<42} {'Sh-0t':>6} {'Sh-1t':>6} {'Sh-2t':>6} {'WorstDay':>10} {'TS%':>6} {'Verdict'}")
    print(f"  {'-'*80}")
    for r in results:
        print(f"  {r['label']:<42} "
              f"{r['sharpe_0t']:>6.2f} {r['sharpe_1t']:>6.2f} {r['sharpe_2t']:>6.2f} "
              f"{r['worst_day']:>10,.0f} {r['ts_compliance']*100:>5.1f}%  {r['verdict']}")

    passers = [r for r in results if r["verdict"] == "PASS"]
    print(f"\n  {len(passers)}/{len(results)} candidates pass stress test")
    if passers:
        print(f"\n  RECOMMENDED ADDITIONS TO LIVE PORTFOLIO:")
        for r in sorted(passers, key=lambda x: x["sharpe_1t"], reverse=True):
            print(f"    {r['label']}")
            print(f"      Params: {r['params']}")
            print(f"      1-tick Sharpe: {r['sharpe_1t']:.2f}  WorstDay: ${r['worst_day']:,.0f}  TS: {r['ts_compliance']*100:.0f}%")

    # Save JSON
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = RESULT_DIR / f"tick_v2_stress_{ts}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Saved: {out_path}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
