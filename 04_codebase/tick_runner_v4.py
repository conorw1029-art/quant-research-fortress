"""WFO runner for V4 strategies. Same pipeline as v1/v2/v3."""
import json, sys, warnings
from datetime import datetime
from itertools import product
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import SPECS, compute_atr, run_backtest
from tick_strategies_v4 import STRATEGIES_V4

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"
OUT_DIR = ROOT / "05_backtests"; OUT_DIR.mkdir(exist_ok=True)

SYMBOLS   = ["GC", "SI", "ES", "NQ"]
BAR_SIZES = [1, 3, 5, 15, 30]
WFO_TRAIN = 2000; WFO_TEST = 500
MAX_GRID  = 12;   DSR_THRESH = 1.0
STOP_MULT = 1.5;  TP_MULT   = 3.0


def load_bars(sym, bar_min):
    p = BAR_DIR / f"{sym}_bars_{bar_min}m.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def dsr(sharpe, n_params):
    return sharpe / np.sqrt(np.log(max(n_params, 2)))


def run_wfo(df, strat, symbol):
    spec  = SPECS.get(symbol, SPECS["GC"])
    fn    = strat["compute"]
    keys  = list(strat["param_grid"].keys())
    vals  = list(strat["param_grid"].values())
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

    best_sr, best_params = -np.inf, dict(zip(keys, combos[0]))
    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            sig = fn(df.iloc[:WFO_TRAIN], **params)
            tr  = run_backtest(df.iloc[:WFO_TRAIN], sig, symbol,
                               stop_atr_mult=STOP_MULT, tp_atr_mult=TP_MULT)
            if tr.empty or len(tr) < 10: continue
            r  = tr["dollar_pnl"] / (spec["point_value"] * STOP_MULT * compute_atr(
                df.iloc[:WFO_TRAIN]["high"].values, df.iloc[:WFO_TRAIN]["low"].values,
                df.iloc[:WFO_TRAIN]["close"].values)[-1])
            sr = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
            if sr > best_sr: best_sr, best_params = sr, params
        except Exception: continue

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
    return {"oos_sharpe": round(sr,3), "dsr": round(d,3), "n_trades": len(oos_pnls),
            "total_pnl": round(sum(oos_pnls),2), "best_params": best_params}


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    results = []; n_tested = n_pass = 0
    print(f"\nV4 WFO — {len(STRATEGIES_V4)} strategies | DSR>={DSR_THRESH}\n")

    for strat in STRATEGIES_V4:
        for symbol in SYMBOLS:
            if strat.get("requires_mbp") and symbol not in ("GC", "SI"): continue
            for bar_min in BAR_SIZES:
                df = load_bars(symbol, bar_min)
                if df is None: continue
                try: r = run_wfo(df, strat, symbol)
                except Exception: r = None
                n_tested += 1
                label = f"{symbol}/{strat['name']}/{bar_min}m"
                if r is None:
                    print(f"  [{n_tested:>3}] {label:<50} SKIP")
                    results.append({"label": label, "skip": "insufficient"})
                    continue
                grade = "EXCELLENT" if r["dsr"]>=2.0 else "GOOD" if r["dsr"]>=1.5 else "MARGINAL" if r["dsr"]>=1.0 else "FAIL"
                r["grade"] = grade; r["label"] = label
                if r["dsr"] >= DSR_THRESH:
                    n_pass += 1
                    print(f"  [{n_tested:>3}] {label:<50} *** DSR={r['dsr']:.2f} ({grade})  Sharpe={r['oos_sharpe']:.2f}  n={r['n_trades']}  PnL=${r['total_pnl']:,.0f}")
                else:
                    print(f"  [{n_tested:>3}] {label:<50} DSR={r['dsr']:.2f} FAIL")
                results.append(r)

    out = OUT_DIR / f"tick_results_v4_{ts}.json"
    with open(out, "w") as f: json.dump(results, f, indent=2)
    print(f"\nDone. Tested={n_tested}  Pass={n_pass}\nResults -> {out}\n")

    survivors = sorted([r for r in results if isinstance(r.get("dsr"),float) and r["dsr"]>=DSR_THRESH], key=lambda x: -x["dsr"])
    if survivors:
        print("=== V4 SURVIVORS ===")
        for s in survivors:
            print(f"  {s['label']:<50} DSR={s['dsr']:.2f}  Sharpe={s['oos_sharpe']:.2f}  PnL=${s['total_pnl']:,.0f}")
    return out, survivors


if __name__ == "__main__":
    main()
