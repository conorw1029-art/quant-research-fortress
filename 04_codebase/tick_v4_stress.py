"""Stress test top V4 candidates. Same criteria as v1-v3."""
import sys, warnings
from datetime import datetime
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
from tick_deep_analysis import run_backtest_slippage
from tick_strategies_v4 import STRAT_MAP_V4

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
STOP_MULT = 1.5; TP_MULT = 3.0; TS_LIMIT = 4500

CANDIDATES = [
    ("GC", 30, "trade_absorption_signal",     {"ntrades_z": 1.2, "range_z": -0.3, "cvd_z": 0.4}),
    ("ES", 30, "volume_ratio_persistence",     {"ratio_thresh": 0.15, "min_streak": 3}),
    ("NQ", 30, "trade_absorption_signal",     {"ntrades_z": 1.2, "range_z": -0.3, "cvd_z": 0.4}),
    ("ES",  5, "trade_absorption_signal",     {"ntrades_z": 1.2, "range_z": -0.3, "cvd_z": 0.4}),
    ("ES", 30, "avg_order_size_divergence",   {"window": 20, "z_thresh": 1.0, "price_thresh": 0.001}),
    ("NQ", 15, "book_pressure_reversal",      {"bp_window": 15, "extreme_z": 1.5, "reversal_z": 0.5}),
    ("GC",  5, "book_pressure_reversal",      {"bp_window": 15, "extreme_z": 1.5, "reversal_z": 0.5}),
    ("GC",  3, "trade_absorption_signal",     {"ntrades_z": 1.2, "range_z": -0.3, "cvd_z": 0.4}),
]

PV = {"GC": 100.0, "SI": 50.0, "ES": 50.0, "NQ": 20.0}


def load_bars(sym, bar_min):
    p = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def stress(df, fn, params, symbol):
    pv = PV.get(symbol, 50.0)
    results = {}
    for ticks in [0, 0.5, 1.0, 2.0]:
        sig = fn(df, **params)
        tr  = run_backtest_slippage(df, sig, symbol, stop_mult=STOP_MULT, tp_mult=TP_MULT, extra_ticks=ticks)
        if tr.empty or len(tr) < 5:
            results[ticks] = None; continue
        r  = tr["dollar_pnl"] / (pv * STOP_MULT)
        sr = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
        results[ticks] = {"sharpe": sr, "wr": (tr["dollar_pnl"]>0).mean(), "n": len(tr), "total": tr["dollar_pnl"].sum()}
    return results


def regimes(df, fn, params, symbol):
    res = {}
    for yr in sorted(df.index.year.unique()):
        sub = df[df.index.year == yr]
        if len(sub) < 200: continue
        sig = fn(sub, **params)
        tr  = run_backtest_slippage(sub, sig, symbol, stop_mult=STOP_MULT, tp_mult=TP_MULT, extra_ticks=0.5)
        res[yr] = tr["dollar_pnl"].sum() if not tr.empty and len(tr) >= 3 else None
    return res


def ts_check(df, fn, params, symbol):
    sig = fn(df, **params)
    tr  = run_backtest_slippage(df, sig, symbol, stop_mult=STOP_MULT, tp_mult=TP_MULT, extra_ticks=0.5)
    if tr.empty or "entry_time" not in tr.columns: return 0.0, 0.0
    tr["date"] = pd.to_datetime(tr["entry_time"]).dt.date
    daily = tr.groupby("date")["dollar_pnl"].sum()
    return (daily >= -TS_LIMIT).mean(), daily.min()


def main():
    print(f"\n{'='*70}\n  V4 STRESS TEST — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{'='*70}\n")
    passed = []

    for symbol, bar_min, strat_name, params in CANDIDATES:
        df = load_bars(symbol, bar_min)
        if df is None:
            print(f"  SKIP {symbol}/{strat_name}/{bar_min}m\n"); continue

        fn    = STRAT_MAP_V4[strat_name]["compute"]
        label = f"{symbol}/{strat_name}/{bar_min}m"
        print(f"\n  {label}  |  Params: {params}")
        print(f"  {'─'*60}")

        slip = stress(df, fn, params, symbol)
        print(f"  Slippage  | Sharpe  | WR%   | Trades")
        for t, r in slip.items():
            if r is None: print(f"  {t:.1f}t       | INSUFF  |       |")
            else: print(f"  {t:.1f}t       | {r['sharpe']:>6.2f}  | {r['wr']*100:>5.1f}% | {r['n']}")

        reg = regimes(df, fn, params, symbol)
        pos  = sum(1 for v in reg.values() if v and v > 0)
        tot  = len([v for v in reg.values() if v is not None])
        print(f"\n  Regimes: {pos}/{tot} years positive")
        for yr, pnl in reg.items():
            flag = "+" if pnl and pnl > 0 else "-"
            print(f"    {yr}: {flag} ${pnl:,.0f}" if pnl is not None else f"    {yr}: no trades")

        ts_pct, worst = ts_check(df, fn, params, symbol)
        print(f"\n  Topstep compliance: {ts_pct*100:.1f}%  |  Worst day: ${worst:,.0f}")

        sr1t = (slip.get(1.0) or {}).get("sharpe", 0)
        ok   = sr1t >= 1.0 and (pos/max(tot,1)) >= 0.70 and ts_pct >= 0.95
        print(f"\n  {'PASS' if ok else 'FAIL'}  (1t-Sharpe={sr1t:.2f}, {pos}/{tot} regimes, TS={ts_pct*100:.0f}%)")

        if ok:
            passed.append({"label": label, "symbol": symbol, "bar_min": bar_min,
                           "strat": strat_name, "params": params,
                           "sr1t": sr1t, "regimes": f"{pos}/{tot}",
                           "ts": ts_pct, "worst": worst})

    print(f"\n{'='*70}\n  V4 PASSED: {len(passed)}/{len(CANDIDATES)}\n{'='*70}")
    for r in passed:
        print(f"  PASS: {r['label']}")
        print(f"        1t-Sharpe={r['sr1t']:.2f}  Regimes={r['regimes']}  TS={r['ts']*100:.0f}%  Worst=${r['worst']:,.0f}")
        print(f"        Params: {r['params']}")
    print()
    return passed


if __name__ == "__main__":
    main()
