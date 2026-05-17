"""
WFO Runner for V3 strategies.
10 strategies × 4 symbols × 5 bar sizes = 200 combinations.
Same pipeline as v1/v2: WFO → DSR grading → JSON output.
"""

import json
import sys
import warnings
from datetime import datetime
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

from tick_backtest_engine import SPECS, compute_atr, run_backtest
from tick_strategies_v3 import STRATEGIES_V3

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
OUT_DIR = ROOT / "05_backtests"
OUT_DIR.mkdir(exist_ok=True)

SYMBOLS   = ["GC", "SI", "ES", "NQ"]
BAR_SIZES = [1, 3, 5, 15, 30]

WFO_TRAIN_BARS = 2000
WFO_TEST_BARS  = 500
MAX_GRID_COMBOS = 12
DSR_THRESHOLD   = 1.0

STOP_MULT = 1.5
TP_MULT   = 3.0


def load_bars(symbol, bar_min):
    p = BAR_DIR / f"{symbol}_bars_{bar_min}m.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def dsr(sharpe, n_params):
    k = max(n_params, 2)
    return sharpe / np.sqrt(np.log(k))


def run_wfo(df, strat, symbol):
    spec     = SPECS.get(symbol, SPECS["GC"])
    fn       = strat["compute"]
    grid_raw = strat["param_grid"]
    keys     = list(grid_raw.keys())
    vals     = list(grid_raw.values())
    combos   = list(product(*vals))
    if len(combos) > MAX_GRID_COMBOS:
        rng    = np.random.default_rng(42)
        idx    = rng.choice(len(combos), MAX_GRID_COMBOS, replace=False)
        combos = [combos[i] for i in sorted(idx)]

    n = len(df)
    folds = []
    start = 0
    while start + WFO_TRAIN_BARS + WFO_TEST_BARS <= n:
        train = df.iloc[start : start + WFO_TRAIN_BARS]
        test  = df.iloc[start + WFO_TRAIN_BARS : start + WFO_TRAIN_BARS + WFO_TEST_BARS]
        folds.append((train, test))
        start += WFO_TEST_BARS

    if not folds:
        return None

    # Best params on train fold 0
    best_sr, best_params = -np.inf, dict(zip(keys, combos[0]))
    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            sig    = fn(df.iloc[:WFO_TRAIN_BARS], **params)
            trades = run_backtest(df.iloc[:WFO_TRAIN_BARS], sig, symbol,
                                  stop_atr_mult=STOP_MULT, tp_atr_mult=TP_MULT)
            if trades.empty or len(trades) < 10:
                continue
            r  = trades["dollar_pnl"] / (spec["point_value"] * STOP_MULT * compute_atr(df.iloc[:WFO_TRAIN_BARS]).iloc[-1])
            sr = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
            if sr > best_sr:
                best_sr, best_params = sr, params
        except Exception:
            continue

    # Collect OOS performance across all folds
    oos_pnls = []
    for train, test in folds:
        try:
            sig    = fn(pd.concat([train, test]), **best_params).iloc[len(train):]
            trades = run_backtest(pd.concat([train, test]), fn(pd.concat([train, test]), **best_params),
                                  symbol, stop_atr_mult=STOP_MULT, tp_atr_mult=TP_MULT)
            # Filter to test period
            if "entry_time" in trades.columns:
                t0 = test.index[0]
                t1 = test.index[-1]
                trades = trades[
                    pd.to_datetime(trades["entry_time"], utc=True).between(t0, t1)
                ]
            oos_pnls.extend(trades["dollar_pnl"].tolist())
        except Exception:
            continue

    if len(oos_pnls) < 10:
        return None

    pnl_s = pd.Series(oos_pnls)
    r_s   = pnl_s / (spec["point_value"] * STOP_MULT)
    sr    = r_s.mean() / r_s.std() * np.sqrt(252) if r_s.std() > 0 else 0
    d     = dsr(sr, len(keys))

    return {
        "oos_sharpe":  round(sr,  3),
        "dsr":         round(d,   3),
        "n_trades":    len(oos_pnls),
        "total_pnl":   round(sum(oos_pnls), 2),
        "best_params": best_params,
    }


def main():
    ts  = datetime.now().strftime("%Y%m%d_%H%M")
    all_results = []
    n_tested = 0
    n_pass   = 0

    print(f"\nV3 WFO Runner — {len(STRATEGIES_V3)} strategies × {len(SYMBOLS)} symbols × {len(BAR_SIZES)} bars")
    print(f"Max {MAX_GRID_COMBOS} param combos per run | DSR threshold {DSR_THRESHOLD}\n")

    for strat in STRATEGIES_V3:
        for symbol in SYMBOLS:
            if strat.get("requires_mbp") and symbol not in ("GC", "SI"):
                continue
            for bar_min in BAR_SIZES:
                df = load_bars(symbol, bar_min)
                if df is None:
                    continue
                try:
                    r = run_wfo(df, strat, symbol)
                except Exception as e:
                    r = None

                n_tested += 1
                label = f"{symbol}/{strat['name']}/{bar_min}m"

                if r is None:
                    status = "SKIP (insufficient data)"
                    print(f"  [{n_tested:>3}] {label:<45} {status}")
                    all_results.append({"label": label, "skip": status})
                    continue

                grade = ("EXCELLENT" if r["dsr"] >= 2.0 else
                         "GOOD"      if r["dsr"] >= 1.5 else
                         "MARGINAL"  if r["dsr"] >= 1.0 else
                         "FAIL")
                r["grade"] = grade
                r["label"] = label

                if r["dsr"] >= DSR_THRESHOLD:
                    n_pass += 1
                    status = f"DSR={r['dsr']:.2f} ({grade})  OOS_Sharpe={r['oos_sharpe']:.2f}  n={r['n_trades']}  PnL=${r['total_pnl']:,.0f}"
                    print(f"  [{n_tested:>3}] {label:<45} *** {status}")
                else:
                    skip_str = f"DSR={r['dsr']:.2f} FAIL"
                    print(f"  [{n_tested:>3}] {label:<45} {skip_str}")

                all_results.append(r)

    out_path = OUT_DIR / f"tick_results_v3_{ts}.json"
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)

    print(f"\nDone. Tested: {n_tested}  Survivors (DSR>={DSR_THRESHOLD}): {n_pass}")
    print(f"Results → {out_path}\n")

    survivors = [r for r in all_results if isinstance(r.get("dsr"), float) and r["dsr"] >= DSR_THRESHOLD]
    if survivors:
        print("=== V3 SURVIVORS ===")
        for s in sorted(survivors, key=lambda x: -x["dsr"]):
            print(f"  {s['label']:<45} DSR={s['dsr']:.2f}  Sharpe={s['oos_sharpe']:.2f}  PnL=${s['total_pnl']:,.0f}")

    return out_path, survivors


if __name__ == "__main__":
    main()
