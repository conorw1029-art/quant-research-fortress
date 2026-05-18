"""
WFO runner for V6, V7, V8 strategy libraries.
Tests all 30 new strategies across GC/ES/NQ/SI × bar sizes.
Uses identical WFO+DSR pipeline as v1–v5 runners.
"""
import json, sys, warnings
from datetime import datetime
from itertools import product
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

from tick_backtest_engine import SPECS, compute_atr, run_backtest
from tick_strategies_v6 import STRATEGIES_V6
from tick_strategies_v7 import STRATEGIES_V7
from tick_strategies_v8 import STRATEGIES_V8

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
OUT_DIR = ROOT / "05_backtests"; OUT_DIR.mkdir(exist_ok=True)

SYMBOLS   = ["GC", "SI", "ES", "NQ"]
BAR_SIZES = [1, 3, 5, 15, 30]
WFO_TRAIN = 2000; WFO_TEST = 500
MAX_GRID  = 12;   DSR_THRESH = 1.0
STOP_MULT = 1.5;  TP_MULT   = 3.0

ALL_STRATEGIES = STRATEGIES_V6 + STRATEGIES_V7 + STRATEGIES_V8


def load_bars(sym, bar_min):
    p = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def dsr(sharpe, n_params):
    return sharpe / np.sqrt(np.log(max(n_params, 2)))


def run_wfo(df, strat, symbol):
    spec   = SPECS.get(symbol, SPECS["GC"])
    fn     = strat["compute"]
    keys   = list(strat["param_grid"].keys())
    vals   = list(strat["param_grid"].values())
    combos = list(product(*vals))
    if len(combos) > MAX_GRID:
        rng    = np.random.default_rng(42)
        idx    = rng.choice(len(combos), MAX_GRID, replace=False)
        combos = [combos[i] for i in sorted(idx)]

    n = len(df)
    folds = []
    start = 0
    while start + WFO_TRAIN + WFO_TEST <= n:
        folds.append((df.iloc[start:start+WFO_TRAIN], df.iloc[start+WFO_TRAIN:start+WFO_TRAIN+WFO_TEST]))
        start += WFO_TEST
    if not folds: return None

    # Select best params on first training window
    best_sr, best_params = -np.inf, dict(zip(keys, combos[0]))
    train0 = df.iloc[:WFO_TRAIN]
    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            sig = fn(train0, **params)
            tr  = run_backtest(train0, sig, symbol, stop_atr_mult=STOP_MULT, tp_atr_mult=TP_MULT)
            if tr.empty or len(tr) < 10: continue
            atr_last = compute_atr(train0["high"].values, train0["low"].values, train0["close"].values)[-1]
            r   = tr["dollar_pnl"] / (spec["point_value"] * STOP_MULT * atr_last)
            sr  = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
            if sr > best_sr: best_sr, best_params = sr, params
        except Exception: continue

    # OOS across all folds using best params
    oos_pnls = []
    for train, test in folds:
        try:
            full = pd.concat([train, test])
            sig  = fn(full, **best_params)
            tr   = run_backtest(full, sig, symbol, stop_atr_mult=STOP_MULT, tp_atr_mult=TP_MULT)
            if "entry_time" in tr.columns:
                t0, t1 = test.index[0], test.index[-1]
                tr = tr[pd.to_datetime(tr["entry_time"], utc=True).between(t0, t1)]
            oos_pnls.extend(tr["dollar_pnl"].tolist())
        except Exception: continue

    if len(oos_pnls) < 10: return None
    r_s = pd.Series(oos_pnls) / (spec["point_value"] * STOP_MULT)
    sr  = r_s.mean() / r_s.std() * np.sqrt(252) if r_s.std() > 0 else 0
    d   = dsr(sr, len(keys))
    return {
        "oos_sharpe": round(sr, 3),
        "dsr":        round(d, 3),
        "n_trades":   len(oos_pnls),
        "total_pnl":  round(sum(oos_pnls), 2),
        "best_params": best_params,
    }


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    results = []; n_tested = n_pass = 0
    v6_n = len(STRATEGIES_V6); v7_n = len(STRATEGIES_V7); v8_n = len(STRATEGIES_V8)
    print(f"\nV678 WFO — {len(ALL_STRATEGIES)} strategies "
          f"(V6={v6_n}, V7={v7_n}, V8={v8_n}) | DSR>={DSR_THRESH}\n")

    for strat in ALL_STRATEGIES:
        for symbol in SYMBOLS:
            for bar_min in BAR_SIZES:
                df = load_bars(symbol, bar_min)
                if df is None: continue
                try:
                    r = run_wfo(df, strat, symbol)
                except Exception as e:
                    r = None
                n_tested += 1
                label = f"{symbol}/{strat['name']}/{bar_min}m"
                if r is None:
                    print(f"  [{n_tested:>4}] {label:<55} SKIP")
                    results.append({"label": label, "skip": "insufficient"})
                    continue
                grade = ("EXCELLENT" if r["dsr"] >= 2.0 else
                         "GOOD"      if r["dsr"] >= 1.5 else
                         "MARGINAL"  if r["dsr"] >= 1.0 else "FAIL")
                r["grade"] = grade
                r["label"] = label
                if r["dsr"] >= DSR_THRESH:
                    n_pass += 1
                    print(f"  [{n_tested:>4}] {label:<55} *** DSR={r['dsr']:.2f} ({grade})"
                          f"  Sharpe={r['oos_sharpe']:.2f}  n={r['n_trades']}  PnL=${r['total_pnl']:,.0f}")
                else:
                    print(f"  [{n_tested:>4}] {label:<55} DSR={r['dsr']:.2f} FAIL")
                results.append(r)

    out = OUT_DIR / f"tick_results_v678_{ts}.json"
    with open(out, "w") as f: json.dump(results, f, indent=2)
    print(f"\nDone. Tested={n_tested}  Pass={n_pass}\nResults -> {out}\n")

    survivors = sorted(
        [r for r in results if isinstance(r.get("dsr"), float) and r["dsr"] >= DSR_THRESH],
        key=lambda x: -x["dsr"],
    )
    if survivors:
        print(f"=== V678 SURVIVORS ({len(survivors)}) ===")
        for s in survivors:
            print(f"  {s['label']:<55} DSR={s['dsr']:.2f}  "
                  f"Sharpe={s['oos_sharpe']:.2f}  PnL=${s['total_pnl']:,.0f}")
    else:
        print("No survivors above DSR threshold.")
    return out, survivors


if __name__ == "__main__":
    main()
