"""
Batch 12: Silver Intraday Expansion — Step 1 Stress Test
=========================================================

Silver (SIL) is a confirmed edge market: vwap_reclaim_si is a portfolio survivor.
The 5 proven intraday families from Batch 5/6 were NEVER applied to Silver.
This is the gap: vol_adj_momentum, donchian_intraday, rth_orb, keltner_breakout,
ma_trend_entry — all on SI_1min.csv.

keltner_breakout_gc and ma_trend_entry_gc both passed Step 1 in Batch 5,
so Silver analogs have a genuine chance.
"""
import sys
import os
import json
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from run_strategy import load_data_cached, build_cost_model, run_walk_forward
from src.zoo.registry import get_by_key, DEFAULT_DATA_PATHS, TestMethod
from src.backtesting.metrics import performance_report, evaluate_go_nogo

BATCH12_KEYS = [
    "vol_adj_momentum_si",
    "donchian_intraday_si",
    "rth_orb_si",
    "keltner_breakout_si",
    "ma_trend_entry_si",
]

COST_SCENARIO = "realistic"
RESULTS_PATH = os.path.join(os.path.dirname(__file__), "..", "05_backtests", "batch12_results.jsonl")


def run_one(key):
    entry = get_by_key(key)
    if entry is None:
        return {"key": key, "error": "not_found_in_registry"}

    csv_path = DEFAULT_DATA_PATHS.get(entry.data_path_key)
    if csv_path is None:
        return {"key": key, "error": f"no data path for {entry.data_path_key}"}
    csv_path = os.path.normpath(os.path.join(os.path.dirname(__file__), csv_path))

    try:
        _, data = load_data_cached(csv_path, entry.timeframe, instrument=entry.instrument)
    except Exception as e:
        return {"key": key, "error": f"load_data: {e}"}

    try:
        cost_model = build_cost_model(entry.instrument, COST_SCENARIO)
        result = run_walk_forward(entry, data, cost_model)
    except Exception as e:
        return {"key": key, "error": f"wfo: {e}\n{traceback.format_exc()}"}

    trades_df = result.combined_oos_trades
    n_trials = result.total_param_combos

    if trades_df is None or trades_df.empty:
        return {"key": key, "verdict": "FAIL", "reason": "no_trades", "n": 0}

    if "net_pnl" not in trades_df.columns:
        return {"key": key, "verdict": "FAIL", "reason": "no_net_pnl_column", "n": len(trades_df)}

    pnl_arr = trades_df["net_pnl"].values

    import pandas as pd
    entry_times = pd.to_datetime(trades_df["entry_time"])
    span_days = max((entry_times.max() - entry_times.min()).days, 1)
    tpy_est = len(pnl_arr) / (span_days / 365.25)

    try:
        point_value = None
        try:
            from src.data.data_schema import INSTRUMENTS
            instr = INSTRUMENTS.get(entry.instrument)
            if instr:
                point_value = instr.point_value
        except Exception:
            pass
        if point_value is None:
            from src.instruments.registry import get_instrument
            instr = get_instrument(entry.instrument)
            point_value = instr.point_value

        report = performance_report(pnl_arr, trades_per_year=tpy_est,
                                    n_trials=n_trials,
                                    instrument_point_value=point_value)
        gng = evaluate_go_nogo(report)
        s = report["standard"]
        d = report["dsr"]
        return {
            "key": key,
            "instrument": entry.instrument,
            "n": s["n_trades"],
            "dsr": d["dsr"],
            "pf": s["profit_factor"],
            "p": s["p_value"],
            "both_halves": bool(s["both_halves_positive"]),
            "mean_pnl": s["mean_pnl"],
            "dd_usd": s["max_drawdown_abs"] * point_value,
            "n_trials": n_trials,
            "tpy": tpy_est,
            "verdict": gng["verdict"],
            "failures": gng["failures"],
        }
    except Exception as e:
        return {"key": key, "error": f"metrics: {e}\n{traceback.format_exc()}"}


def main():
    print("=" * 65)
    print("  BATCH 12: SILVER INTRADAY EXPANSION")
    print(f"  Started: {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 65)

    results = []
    for key in BATCH12_KEYS:
        print(f"\n>>> {key}")
        r = run_one(key)
        results.append(r)
        if "error" in r:
            print(f"  ERROR: {r['error'][:200]}")
        else:
            print(f"  DSR={r.get('dsr',0):+.3f}  PF={r.get('pf',0):.3f}  "
                  f"n={r.get('n',0)}  verdict={r.get('verdict','?')}  "
                  f"failures={r.get('failures',[])}")

    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        for r in results:
            f.write(json.dumps(r, default=str) + "\n")

    print("\n" + "=" * 65)
    print("  BATCH 12 SUMMARY")
    print("=" * 65)
    for r in results:
        v = r.get("verdict", r.get("error", "ERROR")[:30])
        print(f"  {r['key']:<35s}  {v:<10}  DSR={r.get('dsr', 0):+.3f}")
    print(f"\nResults: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
