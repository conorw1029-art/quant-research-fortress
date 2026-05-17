"""
Stress test top V3 candidates.
Same pass criteria as v1/v2:
  1-tick Sharpe > 1.0 AND >=70% regimes profitable AND TS compliance >=95%
"""
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

from tick_deep_analysis import run_backtest_slippage
from tick_strategies_v3 import STRAT_MAP_V3

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"

STOP_MULT = 1.5
TP_MULT   = 3.0

CANDIDATES = [
    # (symbol, bar_min, strat_name, params)
    ("NQ", 30, "range_contraction_break", {"squeeze_pct": 30, "breakout_z": 1.0, "cvd_z": 0.5}),
    ("SI", 15, "range_contraction_break", {"squeeze_pct": 30, "breakout_z": 1.0, "cvd_z": 0.5}),
    ("GC",  3, "session_momentum_follow", {"bias_z": 1.0, "follow_bars": 8, "break_pct": 0.0002}),
    ("GC", 30, "opening_range_bias",      {"or_bars": 2, "cvd_z_thresh": 0.5, "breakout_pct": 0.0003}),
    ("GC",  5, "session_momentum_follow", {"bias_z": 1.0, "follow_bars": 8, "break_pct": 0.0002}),
    ("GC", 30, "range_contraction_break", {"squeeze_pct": 30, "breakout_z": 1.0, "cvd_z": 0.5}),
    ("SI", 15, "delta_exhaustion_level",  {"level_window": 20, "delta_z": 1.5, "proximity_pct": 0.002}),
    ("SI", 30, "break_retest_cvd",        {"level_window": 20, "retest_bars": 5, "atr_mult": 0.5}),
    ("NQ", 15, "break_retest_cvd",        {"level_window": 20, "retest_bars": 5, "atr_mult": 0.5}),
    ("ES", 15, "cvd_roc_divergence",      {"price_window": 10, "cvd_window": 10, "roc_thresh": 0.3}),
]

TOPSTEP_DAILY_LIMIT = 4500


def load_bars(symbol, bar_min):
    p = BAR_DIR / f"{symbol}_bars_{bar_min}m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def slippage_test(df, fn, params, symbol, label):
    results = {}
    for ticks in [0, 0.5, 1.0, 2.0]:
        sig = fn(df, **params)
        tr  = run_backtest_slippage(df, sig, symbol,
                                     stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                     extra_ticks=ticks)
        if tr.empty or len(tr) < 5:
            results[ticks] = None
            continue
        pv   = {"GC": 100.0, "SI": 50.0, "ES": 50.0, "NQ": 20.0}.get(symbol, 50.0)
        r    = tr["dollar_pnl"] / (pv * STOP_MULT)
        sr   = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
        wr   = (tr["dollar_pnl"] > 0).mean()
        results[ticks] = {"sharpe": sr, "wr": wr, "n": len(tr), "total": tr["dollar_pnl"].sum()}
    return results


def regime_test(df, fn, params, symbol):
    if not hasattr(df.index, 'year'):
        return {}
    results = {}
    for yr in sorted(df.index.year.unique()):
        sub = df[df.index.year == yr]
        if len(sub) < 200:
            continue
        sig = fn(sub, **params)
        tr  = run_backtest_slippage(sub, sig, symbol,
                                     stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                     extra_ticks=0.5)
        if tr.empty or len(tr) < 5:
            results[yr] = None
            continue
        results[yr] = tr["dollar_pnl"].sum()
    return results


def topstep_compliance(df, fn, params, symbol):
    sig = fn(df, **params)
    tr  = run_backtest_slippage(df, sig, symbol,
                                 stop_mult=STOP_MULT, tp_mult=TP_MULT,
                                 extra_ticks=0.5)
    if tr.empty or "entry_time" not in tr.columns:
        return 0.0
    tr["date"] = pd.to_datetime(tr["entry_time"]).dt.date
    daily = tr.groupby("date")["dollar_pnl"].sum()
    worst = daily.min()
    pct   = (daily >= -TOPSTEP_DAILY_LIMIT).mean()
    return pct, worst


def main():
    print(f"\n{'='*70}")
    print(f"  V3 STRESS TEST — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}\n")

    pass_count = 0
    results = []

    for symbol, bar_min, strat_name, params in CANDIDATES:
        df = load_bars(symbol, bar_min)
        if df is None:
            print(f"  SKIP {symbol}/{strat_name}/{bar_min}m — no data\n")
            continue

        fn    = STRAT_MAP_V3[strat_name]["compute"]
        label = f"{symbol}/{strat_name}/{bar_min}m"

        print(f"\n  {label}")
        print(f"  Params: {params}")
        print(f"  {'─'*60}")

        # 1. Slippage ladder
        slip = slippage_test(df, fn, params, symbol, label)
        print(f"  Slippage  | Sharpe  | WR%   | Trades")
        for ticks, r in slip.items():
            if r is None:
                print(f"  {ticks:.1f}t       | INSUFF  |       |")
            else:
                print(f"  {ticks:.1f}t       | {r['sharpe']:>6.2f}  | {r['wr']*100:>5.1f}% | {r['n']}")

        # 2. Annual regime breakdown
        reg = regime_test(df, fn, params, symbol)
        pos_years = sum(1 for v in reg.values() if v is not None and v > 0)
        total_years = len([v for v in reg.values() if v is not None])
        print(f"\n  Regimes (years): {pos_years}/{total_years} positive")
        for yr, pnl in reg.items():
            flag = "+" if pnl and pnl > 0 else "-"
            pnl_str = f"${pnl:,.0f}" if pnl is not None else "no trades"
            print(f"    {yr}: {flag} {pnl_str}")

        # 3. Topstep compliance
        ts_result = topstep_compliance(df, fn, params, symbol)
        if isinstance(ts_result, tuple):
            ts_pct, worst_day = ts_result
            print(f"\n  Topstep compliance: {ts_pct*100:.1f}%  |  Worst day: ${worst_day:,.0f}")
        else:
            ts_pct, worst_day = 0.0, 0.0
            print(f"\n  Topstep compliance: n/a")

        # Pass/fail
        one_tick_sr = (slip.get(1.0) or {}).get("sharpe", 0)
        regime_ok   = (pos_years / max(total_years, 1)) >= 0.70
        ts_ok       = ts_pct >= 0.95

        passed = one_tick_sr >= 1.0 and regime_ok and ts_ok
        status = "PASS" if passed else "FAIL"
        print(f"\n  Result: {status}  (1t_Sharpe={one_tick_sr:.2f}, regimes={pos_years}/{total_years}, TS={ts_pct*100:.1f}%)")

        if passed:
            pass_count += 1
            results.append({
                "label": label, "symbol": symbol, "bar_min": bar_min,
                "strat_name": strat_name, "params": params,
                "one_tick_sharpe": one_tick_sr,
                "regimes": f"{pos_years}/{total_years}",
                "ts_compliance": ts_pct,
                "worst_day": worst_day,
            })

    print(f"\n{'='*70}")
    print(f"  V3 STRESS RESULTS: {pass_count}/{len(CANDIDATES)} PASS")
    print(f"{'='*70}")
    for r in results:
        print(f"  PASS: {r['label']}")
        print(f"        1t-Sharpe={r['one_tick_sharpe']:.2f}  Regimes={r['regimes']}  TS={r['ts_compliance']*100:.0f}%  WorstDay=${r['worst_day']:,.0f}")
        print(f"        Params: {r['params']}")
    print()


if __name__ == "__main__":
    main()
