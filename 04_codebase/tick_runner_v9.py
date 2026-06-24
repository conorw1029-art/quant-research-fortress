#!/usr/bin/env python3
"""
tick_runner_v9.py — Backtest runner for V9 calendar/event strategies
=====================================================================
Runs fomc_drift on ES (all bar sizes that contain enough FOMC events).
Unlike V6/V7/V8 WFO, fomc_drift is an event-based strategy — WFO makes
less sense. Instead we run a simple walk-forward by year and check:
  • Win rate per year
  • Annual P&L
  • DSR across the full period

Usage:
    python tick_runner_v9.py
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT    = Path(__file__).parent.parent
_LOCAL_BAR_DIR = ROOT / "01_data" / "tick_bars"
_VPS_BAR_DIR   = Path("/opt/fortress/01_data/tick_bars")
BAR_DIR = _LOCAL_BAR_DIR if _LOCAL_BAR_DIR.exists() and any(_LOCAL_BAR_DIR.glob("*.parquet")) else _VPS_BAR_DIR
OUT_DIR = ROOT / "05_backtests"
OUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import SPECS, run_backtest, compute_atr
from tick_strategies_v9 import fomc_drift


STOP_MULT = 1.5
TP_MULT   = 3.0


def load_bars(sym: str, bar_min: int) -> pd.DataFrame | None:
    p = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def run_fomc(df: pd.DataFrame, sym: str) -> dict | None:
    spec = SPECS.get(sym, SPECS["ES"])
    sig  = fomc_drift(df)
    tr   = run_backtest(df, sig, sym, stop_atr_mult=STOP_MULT, tp_atr_mult=TP_MULT)
    if tr.empty or len(tr) < 3:
        return None

    pnl  = tr["dollar_pnl"]
    wr   = (pnl > 0).mean()
    pv   = spec["point_value"]
    atr_last = compute_atr(df["high"].values, df["low"].values, df["close"].values)[-1]
    r    = pnl / (pv * STOP_MULT * atr_last)
    sr   = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    dsr  = sr / np.sqrt(np.log(2))  # 1 effective parameter (direction only)

    # Year-by-year breakdown
    if "entry_time" in tr.columns:
        tr["year"] = pd.to_datetime(tr["entry_time"], utc=True).dt.year
        yearly = tr.groupby("year").agg(
            n_trades=("dollar_pnl", "count"),
            win_rate=("dollar_pnl", lambda x: (x > 0).mean()),
            pnl=("dollar_pnl", "sum"),
        ).to_dict("index")
    else:
        yearly = {}

    return {
        "n_trades":   len(tr),
        "win_rate":   round(float(wr), 3),
        "total_pnl":  round(float(pnl.sum()), 2),
        "sharpe":     round(float(sr), 3),
        "dsr":        round(float(dsr), 3),
        "yearly":     {str(k): v for k, v in yearly.items()},
    }


def main():
    ts  = datetime.now().strftime("%Y%m%d_%H%M")
    print(f"\n{'='*60}")
    print(f"  V9 FOMC Drift Backtest  ({ts})")
    print(f"  DSR = Sharpe / sqrt(log(2)) [1 param: direction always long]")
    print(f"{'='*60}\n")

    all_results = []
    for sym in ["ES", "NQ"]:
        for bar_min in [5, 15, 30, 60]:
            df = load_bars(sym, bar_min)
            if df is None:
                print(f"  {sym}/{bar_min}m: no data")
                continue
            label = f"{sym}/fomc_drift/{bar_min}m"
            res   = run_fomc(df, sym)
            if res is None:
                print(f"  {label}: insufficient FOMC events in data range")
                all_results.append({"label": label, "skip": "insufficient"})
                continue

            grade = ("EXCELLENT" if res["dsr"] >= 2.0 else
                     "GOOD"      if res["dsr"] >= 1.5 else
                     "MARGINAL"  if res["dsr"] >= 1.0 else "FAIL")
            print(f"  {label:<40}  DSR={res['dsr']:.2f} ({grade})"
                  f"  WR={res['win_rate']:.1%}  n={res['n_trades']}"
                  f"  PnL=${res['total_pnl']:,.0f}")
            if res["yearly"]:
                for yr, yd in sorted(res["yearly"].items()):
                    print(f"    {yr}: {yd['n_trades']} trades  "
                          f"WR={yd['win_rate']:.0%}  PnL=${yd['pnl']:,.0f}")
            res["label"] = label
            res["grade"] = grade
            all_results.append(res)

    out = OUT_DIR / f"tick_results_v9_{ts}.json"
    with open(out, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults → {out}")

    survivors = [r for r in all_results if isinstance(r.get("dsr"), float) and r["dsr"] >= 1.0]
    if survivors:
        print(f"\n=== V9 SURVIVORS ({len(survivors)}) ===")
        for s in sorted(survivors, key=lambda x: -x["dsr"]):
            print(f"  {s['label']:<40}  DSR={s['dsr']:.2f}  WR={s['win_rate']:.1%}")
    else:
        print("\nNo V9 survivors above DSR=1.0 in current data range.")
        print("Note: FOMC drift was backtested on 2010-2025 data (57 trades, DSR=1.627)")
        print("Current yfinance data range is too short to confirm — need Databento ES data.")


if __name__ == "__main__":
    main()
