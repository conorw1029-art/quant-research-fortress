"""
Batch 11: Daily Trend Exhaustion — Remaining Markets
Tests daily trend strategies on the last untested markets:
  ZF (5yr T-Note), ZC (Corn), ZW (Wheat), 6C (Canadian Dollar), 6A (Australian Dollar).

Strategy families:
  - donchian_breakout (daily channel breakout — proven on CL)
  - tsm (time-series momentum — Baltas-Kosowski)

Note: ZC/ZW are restricted at Topstep but tested for completeness.
If a survivor emerges, Step 2 topstep regime will flag it as CONDITIONAL.

8 tests total.
"""
import sys
import os
import json
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from run_strategy import load_data_cached, build_cost_model, run_walk_forward, run_one_shot_is_oos
from src.zoo.registry import get_by_key, DEFAULT_DATA_PATHS, TestMethod
from src.backtesting.metrics import performance_report, evaluate_go_nogo

BATCH11_KEYS = [
    # Daily Donchian on untested bond + agricultural markets
    "donchian_breakout_zf",
    "donchian_breakout_zc",
    "donchian_breakout_zw",
    # Time-series momentum on untested daily markets
    "tsm_zf",
    "tsm_zc",
    "tsm_zw",
    "tsm_fxc",
    "tsm_fxa",
]

COST_SCENARIO = "realistic"
RESULTS_PATH = os.path.join(os.path.dirname(__file__), "..", "05_backtests", "batch11_results.jsonl")


def run_one(key):
    entry = get_by_key(key)
    if entry is None:
        return {"key": key, "error": "not_found_in_registry"}

    csv_path = DEFAULT_DATA_PATHS[entry.data_path_key]
    csv_path = os.path.normpath(os.path.join(os.path.dirname(__file__), csv_path))

    try:
        _, data = load_data_cached(csv_path, entry.timeframe, instrument=entry.instrument)
    except Exception as e:
        return {"key": key, "error": f"load_data: {e}"}

    try:
        cost_model = build_cost_model(entry.instrument, COST_SCENARIO)
        if entry.test_method == TestMethod.ONE_SHOT_IS_OOS:
            result = run_one_shot_is_oos(entry, data, cost_model)
        else:
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
    try:
        from src.data.data_schema import INSTRUMENTS
        point_value = INSTRUMENTS[entry.instrument].point_value
    except Exception:
        point_value = 1.0

    try:
        report = performance_report(pnl_arr, trades_per_year=252.0,
                                    n_trials=n_trials, instrument_point_value=point_value)
        gng = evaluate_go_nogo(report)
    except Exception as e:
        return {"key": key, "error": f"metrics: {e}"}

    s = report["standard"]
    d = report["dsr"]
    return {
        "key": key,
        "verdict": gng["verdict"],
        "failures": gng["failures"],
        "dsr": round(d["dsr"], 3),
        "pf": round(s["profit_factor"], 3),
        "n": s["n_trades"],
        "both_halves": bool(s["both_halves_positive"]),
        "max_dd_usd": round(s["max_drawdown_abs"] * point_value, 2),
        "n_trials": n_trials,
        "instrument": entry.instrument,
        "test_method": entry.test_method.value,
    }


def main():
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    print(f"{'='*60}")
    print(f"BATCH 11 — Daily Trend Exhaustion (Remaining Markets)")
    print(f"Markets: ZF (5yr), ZC (Corn), ZW (Wheat), 6C (CAD), 6A (AUD)")
    print(f"Step 1 Stress Test ({COST_SCENARIO} costs)")
    print(f"Started: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")

    survivors = []
    with open(RESULTS_PATH, "w") as f:
        for key in BATCH11_KEYS:
            print(f">>> {key} ...", flush=True)
            result = run_one(key)
            result["timestamp"] = datetime.now().isoformat()
            f.write(json.dumps(result) + "\n")
            f.flush()

            verdict = result.get("verdict", "ERROR")
            if "error" in result:
                print(f"    ERROR: {result['error'][:300]}")
            else:
                dsr = result.get("dsr", 0)
                pf  = result.get("pf", 0)
                n   = result.get("n", 0)
                tm  = result.get("test_method", "")
                failures = result.get("failures", [])
                print(f"    {verdict:6s}  DSR={dsr:+.3f}  PF={pf:.3f}  n={n}  [{tm}]  failures={failures}")
                if verdict == "PASS":
                    survivors.append(key)
            print()

    print(f"{'='*60}")
    print(f"DONE. {len(survivors)}/{len(BATCH11_KEYS)} PASS verdicts.")
    if survivors:
        print("\nSURVIVORS (Step 1 PASS — proceed to Step 2 stress):")
        for s in survivors:
            print(f"  {s}")
    else:
        print("\nNo survivors this batch.")
    print(f"\nFull results: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
