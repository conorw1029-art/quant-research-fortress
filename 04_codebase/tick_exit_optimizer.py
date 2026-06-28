#!/usr/bin/env python3
"""
tick_exit_optimizer.py — find the most profitable STOP / TAKE-PROFIT / HOLD exit
configuration for each strategy. Reuses the proven run_backtest_slippage engine,
sweeping stop_mult × tp_mult × max_hold with realistic slippage, ranked by
after-cost net P&L (min trade count enforced).

⚠️  HONESTY NOTE: results are only as good as the data. On the ~70 days currently
on the VPS these are PRELIMINARY and overfit-prone. The production run is on the
live `trades` (footprint) data once it flows, and ideally a multi-year history.
This engine models plain stop/TP/hold (no ratchet/breakeven) — the RiskManager-
faithful optimizer (with ratchet + breakeven_r) is the next step on real data.
"""
from __future__ import annotations
import argparse, itertools, json
import numpy as np, pandas as pd
from pathlib import Path

from tick_deep_analysis import run_backtest_slippage
from tick_strategies_v6 import STRAT_MAP_V6
from tick_strategies_v7 import STRAT_MAP_V7
from tick_strategies_v8 import STRAT_MAP_V8

DATA      = Path("/opt/fortress/01_data/historical_5m")
STOP_GRID = [1.0, 1.5, 2.0, 2.5]
TP_GRID   = [1.5, 2.0, 3.0, 4.0]
HOLD_GRID = [30, 50, 80]
EXTRA_TICKS = 1.0        # realistic slippage per side
MIN_TRADES  = 20

ALL_MAPS = {"v6": STRAT_MAP_V6, "v7": STRAT_MAP_V7, "v8": STRAT_MAP_V8}


def load(sym: str, tf: int):
    f = DATA / f"{sym}_{tf}m_60d.parquet"
    if not f.exists():
        return None
    df = pd.read_parquet(f)
    df.index = pd.to_datetime(df.index)
    return df


def _metrics(tr: pd.DataFrame) -> dict:
    pnl = tr["dollar_pnl"]
    return {
        "trades":   int(len(tr)),
        "win_rate": round(float((pnl > 0).mean()) * 100, 1),
        "net_pnl":  round(float(pnl.sum()), 0),
        "avg":      round(float(pnl.mean()), 1),
        "sharpe":   round(float(pnl.mean() / (pnl.std() + 1e-9) * np.sqrt(len(tr))), 2),
    }


def best_entry_params(df, strat, symbol):
    """Pick a representative (most-active) entry parameter set so the exit sweep
    is comparing exits, not entries."""
    fn, grid = strat["compute"], strat["param_grid"]
    keys, best, best_n = list(grid), None, 0
    for combo in list(itertools.product(*grid.values()))[:12]:
        p = dict(zip(keys, combo))
        try:
            tr = run_backtest_slippage(df, fn(df, **p), symbol, extra_ticks=EXTRA_TICKS)
        except Exception:
            continue
        if len(tr) > best_n:
            best, best_n = p, len(tr)
    return best


def optimize_one(df, strat, symbol):
    fn = strat["compute"]
    params = best_entry_params(df, strat, symbol)
    if params is None:
        return None
    sig = fn(df, **params)
    rows = []
    for sm, tp, hold in itertools.product(STOP_GRID, TP_GRID, HOLD_GRID):
        try:
            tr = run_backtest_slippage(df, sig, symbol, stop_mult=sm, tp_mult=tp,
                                       max_hold=hold, extra_ticks=EXTRA_TICKS)
        except Exception:
            continue
        if len(tr) < MIN_TRADES:
            continue
        m = _metrics(tr)
        m.update({"stop_mult": sm, "tp_mult": tp, "max_hold": hold, "rr": round(tp / sm, 2)})
        rows.append(m)
    if not rows:
        return None
    rows.sort(key=lambda r: r["net_pnl"], reverse=True)
    return {"entry_params": params, "best": rows[0], "top3": rows[:3]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="GC,SI")
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--out", default="/opt/fortress/05_backtests/exit_optimization.json")
    args = ap.parse_args()

    results = {}
    for sym in args.symbols.split(","):
        df = load(sym, args.tf)
        if df is None:
            print(f"[skip] no data for {sym} {args.tf}m")
            continue
        print(f"\n=== {sym} {args.tf}m  ({len(df)} bars) ===")
        print(f"{'strategy':32s} {'best stop×tp(hold)':20s} {'R:R':>4s} {'n':>4s} {'WR%':>5s} {'net$':>8s} {'shrp':>5s}")
        for ver, smap in ALL_MAPS.items():
            for name, strat in smap.items():
                res = optimize_one(df, strat, sym)
                if not res:
                    continue
                b = res["best"]
                tag = f"{b['stop_mult']}×{b['tp_mult']}({b['max_hold']})"
                print(f"{name[:32]:32s} {tag:20s} {b['rr']:>4} {b['trades']:>4} "
                      f"{b['win_rate']:>5} {b['net_pnl']:>8,.0f} {b['sharpe']:>5}")
                results[f"{sym}/{name}/{ver}/{args.tf}m"] = res

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {len(results)} strategy results → {args.out}")


if __name__ == "__main__":
    main()
