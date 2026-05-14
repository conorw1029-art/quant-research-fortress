"""
STEP 2 Extended Stress Suite — VWAP Reclaim Survivors
======================================================
Stress-tests the new VWAP survivors against 6 regimes:
  1. double_cost   — 2× realistic cost
  2. half_size     — halve position size
  3. slip_shock    — exponential fat-tail slippage
  4. missed_20     — drop 20% of signals randomly
  5. stop_200      — $200/trade hard stop cap
  6. topstep       — Topstep Funded: $1500/day, $2000 trailing DD

Results appended to 05_backtests/zoo_stress_vwap.jsonl
"""

import sys, json, logging, time
from pathlib import Path

import numpy as np
import pandas as pd

THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from run_strategy import load_data_cached, build_cost_model, run_walk_forward
from src.zoo.registry import get_by_key
from src.data.data_schema import INSTRUMENTS
from src.backtesting.metrics import performance_report, evaluate_go_nogo

MIN_DSR   = 1.0
MIN_PF    = 1.25
MAX_P     = 0.05
MAX_DD    = 2000.0
MIN_N     = 30
DSR_WAIVER = 3.0

TOPSTEP_ACCOUNT_SIZE   = 25_000.0
TOPSTEP_MAX_DAILY_LOSS =  1_500.0
TOPSTEP_MAX_TRAIL_DD   =  2_000.0

# Survivors that passed Step 1 (conservative costs)
HARDENED = [
    ("vwap_reclaim_gc",  10.500, 2.820, 1408),  # DSR_cons, PF_cons, n_cons
    ("vwap_reclaim_si",   4.830, 3.007,  402),  # DSR_cons, PF_cons, n_cons
]

ZOO_OUT = THIS_DIR.parent / "05_backtests" / "zoo_stress_vwap.jsonl"


# ── Stress transforms ─────────────────────────────────────────────

def transform_double_cost(trades_df):
    return (trades_df["gross_pnl"] - 2 * trades_df["cost_pts"]).values

def transform_half_size(trades_df):
    return (trades_df["net_pnl"] * 0.5).values

def transform_slippage_shock(trades_df, tick_size, seed=42):
    rng = np.random.RandomState(seed)
    shock = rng.exponential(scale=1.5 * tick_size, size=len(trades_df))
    return (trades_df["net_pnl"].values - shock)

def transform_missed_20pct(trades_df, seed=42):
    rng = np.random.RandomState(seed)
    mask = rng.random(len(trades_df)) >= 0.20
    return trades_df["net_pnl"].values[mask]

def transform_stop_200(trades_df, point_value):
    cap_pts = 200.0 / point_value
    net = trades_df["net_pnl"].values.copy()
    return np.where(net < -cap_pts, -cap_pts, net)


def simulate_topstep(trades_df, point_value):
    if "entry_time" not in trades_df.columns:
        return {"pass": False, "terminal": True, "daily_violations": -1,
                "final_equity": 0, "note": "no entry_time"}

    df = trades_df.copy()
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["trade_date"] = df["entry_time"].dt.date

    equity        = TOPSTEP_ACCOUNT_SIZE
    peak_equity   = TOPSTEP_ACCOUNT_SIZE
    daily_viol    = 0
    account_dead  = False

    for trade_date, day_trades in df.groupby("trade_date"):
        daily_pnl_pts = day_trades["net_pnl"].sum()
        daily_pnl_usd = daily_pnl_pts * point_value

        if daily_pnl_usd < -TOPSTEP_MAX_DAILY_LOSS:
            daily_viol += 1

        equity += daily_pnl_usd
        if equity > peak_equity:
            peak_equity = equity

        trail_dd = peak_equity - equity
        if trail_dd >= TOPSTEP_MAX_TRAIL_DD:
            account_dead = True
            break

    return {
        "pass":             not account_dead,
        "terminal":         account_dead,
        "daily_violations": daily_viol,
        "final_equity":     round(equity, 2),
    }


def eval_transformed(pnl_arr, n_trials, label, trades_per_year=252.0, point_value=1.0):
    if len(pnl_arr) < MIN_N:
        return {
            "label": label, "dsr": 0.0, "pf": 0.0, "dd_usd": 0.0,
            "n": len(pnl_arr), "p": 1.0, "both_halves": False,
            "verdict": "FAIL", "failures": ["insufficient_n"],
        }
    report = performance_report(pnl_arr, trades_per_year=trades_per_year,
                                n_trials=n_trials,
                                instrument_point_value=point_value)
    gng = evaluate_go_nogo(report)
    s   = report["standard"]
    d   = report["dsr"]

    return {
        "label":       label,
        "dsr":         round(d["dsr"], 3),
        "pf":          round(s["profit_factor"], 3),
        "dd_usd":      round(s["max_drawdown_abs"] * point_value, 2),
        "n":           s["n_trades"],
        "p":           round(s["p_value"], 4),
        "both_halves": s["both_halves_positive"],
        "verdict":     gng["verdict"],
        "failures":    gng["failures"],
    }


def run_survivor(key, cons_dsr, cons_pf, cons_n):
    logger.info("")
    logger.info("=" * 65)
    logger.info(f"  Survivor: {key}  cons_DSR=+{cons_dsr}")
    logger.info("=" * 65)

    entry = get_by_key(key)
    if entry is None:
        logger.error(f"  Unknown key: {key}")
        return None

    raw_dir = THIS_DIR.parent / "01_data" / "raw"
    from src.zoo.registry import DEFAULT_DATA_PATHS
    fname   = DEFAULT_DATA_PATHS.get(entry.data_path_key)
    if fname is None:
        logger.error(f"  No data path for {entry.data_path_key}")
        return None
    csv_path = str(raw_dir.parent / fname)

    logger.info(f"  Loading data: {entry.data_path_key}_1min.csv @ {entry.timeframe}")
    _, data = load_data_cached(csv_path, entry.timeframe, instrument=entry.instrument)

    cost_model = build_cost_model(entry.instrument, "realistic")
    instr_spec  = INSTRUMENTS[entry.instrument]
    tick_size   = instr_spec.tick_size
    point_value = instr_spec.point_value

    t0 = time.time()
    result = run_walk_forward(entry, data, cost_model)
    elapsed = time.time() - t0
    logger.info(f"  WFO done in {elapsed:.1f}s")

    trades_df = result.combined_oos_trades
    if trades_df is None or len(trades_df) == 0:
        logger.error(f"  No OOS trades returned")
        return None

    n = len(trades_df)
    n_trials = result.n_trials if hasattr(result, "n_trials") else 20
    logger.info(f"  n_trades={n}, n_trials={n_trials}")

    base_pnl = trades_df["net_pnl"].values
    tpy      = n / (result.n_folds if hasattr(result, "n_folds") else 10) * (252 / 1)
    tests = []

    tests.append(eval_transformed(base_pnl,                                      n_trials, "base(real)", point_value=point_value))
    tests.append(eval_transformed(transform_double_cost(trades_df),               n_trials, "2x_cost",   point_value=point_value))
    tests.append(eval_transformed(transform_half_size(trades_df),                 n_trials, "half_sz",   point_value=point_value))
    tests.append(eval_transformed(transform_slippage_shock(trades_df, tick_size), n_trials, "slip_shk",  point_value=point_value))
    tests.append(eval_transformed(transform_missed_20pct(trades_df),              n_trials, "miss_20%",  point_value=point_value))
    tests.append(eval_transformed(transform_stop_200(trades_df, point_value),     n_trials, "stop$200",  point_value=point_value))

    ts = simulate_topstep(trades_df, point_value)

    # Print report
    header = f"  {'Test':15s} {'DSR':>7} {'PF':>6} {'n':>6}  {'p':>7}  {'BH':>3} Verdict  Failures"
    logger.info(f"\n  --- {key} ({entry.instrument}) ---")
    logger.info(header)
    logger.info("  " + "-" * 80)
    for t in tests:
        bh_s = "Y" if t["both_halves"] else "N"
        fail_s = ", ".join(t["failures"]) if t["failures"] else "-"
        logger.info(f"  {t['label']:15s} {t['dsr']:+7.3f} {t['pf']:6.3f} {t['n']:6d}  {t['p']:7.4f}  {bh_s:>3} {t['verdict']:7s}  {fail_s}")

    ts_verdict = "PASS" if ts["pass"] else "FAIL"
    ts_fail    = f"daily_viol={ts['daily_violations']} terminal={ts['terminal']} final_eq=${ts['final_equity']:,.0f}"
    logger.info(f"  {'topstep':15s} {'n/a':>7} {'n/a':>6} {n:6d}  {'n/a':>7}  {'n/a':>3} {ts_verdict:7s}  {ts_fail}")

    all_verdicts = [t["verdict"] for t in tests] + [ts_verdict]
    overall = "ALL-CLEAR" if all(v == "PASS" for v in all_verdicts) else "CONDITIONAL"
    logger.info(f"\n  >> {key}: {overall}")

    return {"key": key, "tests": tests, "topstep": ts, "overall": overall}


def main():
    logger.info("=" * 65)
    logger.info("  VWAP SURVIVOR STRESS SUITE")
    logger.info("=" * 65)

    results = []
    for (key, cons_dsr, cons_pf, cons_n) in HARDENED:
        if cons_dsr is None:
            logger.info(f"  Skipping {key} — conservative cost result not confirmed")
            continue
        r = run_survivor(key, cons_dsr, cons_pf, cons_n)
        if r:
            results.append(r)

    # Summary
    logger.info("\n" + "=" * 65)
    logger.info("  VWAP STRESS SUMMARY")
    logger.info("=" * 65)
    all_clear  = [r["key"] for r in results if r["overall"] == "ALL-CLEAR"]
    cond       = [r["key"] for r in results if r["overall"] == "CONDITIONAL"]
    logger.info(f"  ALL-CLEAR ({len(all_clear)}): {', '.join(all_clear) or 'none'}")
    logger.info(f"  CONDITIONAL ({len(cond)}): {', '.join(cond) or 'none'}")
    logger.info("=" * 65)

    # Write results
    ZOO_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(ZOO_OUT, "a") as f:
        for r in results:
            f.write(json.dumps(r, default=str) + "\n")
    logger.info(f"\nResults written to: {ZOO_OUT}")


if __name__ == "__main__":
    main()
