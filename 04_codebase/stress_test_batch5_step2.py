"""
Batch 5: Trend Following Family — Step 2 Extended Stress Suite
Runs the 4 Batch 5 Step-1 survivors (all Gold / MGC) through
six stress regimes before deciding on Step 2 verdict.

TESTS:
  1. double_cost   — charge 2x the realistic per-trade cost (~4 ticks/side)
  2. half_size     — halve position size
  3. slip_shock    — fat-tail random slippage per trade (exponential dist)
  4. missed_20     — randomly drop 20% of signals
  5. stop_200      — hard $200 per-trade stop loss
  6. topstep       — simulate Topstep Funded rules ($1500/day limit, $2000 trailing DD)

Results appended to 05_backtests/batch5_step2.jsonl.
"""

import sys, json, logging, time
from pathlib import Path
from typing import List, Optional

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

from run_strategy import load_data_cached, build_cost_model, run_walk_forward, run_one_shot_is_oos
from src.zoo.registry import get_by_key, TestMethod
from src.data.data_schema import INSTRUMENTS
from src.backtesting.metrics import evaluate_go_nogo, performance_report

TOPSTEP_ACCOUNT_SIZE   = 25_000.0
TOPSTEP_MAX_DAILY_LOSS =  1_500.0
TOPSTEP_MAX_TRAIL_DD   =  2_000.0

# Batch 5 Step 1 survivors (all Gold / MGC)
HARDENED = [
    # key                     DSR_step1  PF_step1   n_step1
    ("ma_trend_entry_gc",      1.472,    1.880,      268),
    ("keltner_breakout_gc",    1.701,    1.429,     1107),
    ("vol_adj_momentum_gc",    6.392,    2.070,     1016),
    ("donchian_intraday_gc",   6.593,    1.914,     1327),
]

OUT = THIS_DIR.parent / "05_backtests" / "batch5_step2.jsonl"


def _metrics(net_pnl: np.ndarray, n_trials: int, point_value: float,
             trades_per_year: float) -> dict:
    if len(net_pnl) < 5:
        return {"dsr": 0.0, "pf": 0.0, "dd_usd": 0.0, "n": 0,
                "p": 1.0, "both_halves": False, "mean_pnl": 0.0,
                "verdict": "FAIL", "failures": ["n<5"]}
    report = performance_report(net_pnl, trades_per_year=trades_per_year,
                                n_trials=n_trials,
                                instrument_point_value=point_value)
    gng = evaluate_go_nogo(report)
    s = report["standard"]
    d = report["dsr"]
    return {
        "dsr":        d["dsr"],
        "pf":         s["profit_factor"],
        "dd_usd":     s["max_drawdown_abs"] * point_value,
        "n":          s["n_trades"],
        "p":          s["p_value"],
        "both_halves": bool(s["both_halves_positive"]),
        "mean_pnl":   s["mean_pnl"],
        "verdict":    gng["verdict"],
        "failures":   gng["failures"],
    }


def simulate_topstep(trades_df: pd.DataFrame, point_value: float) -> dict:
    if trades_df.empty or "net_pnl" not in trades_df.columns:
        return {"daily_violations": 0, "account_terminal": False,
                "terminal_trade": -1, "topstep_verdict": "FAIL",
                "peak_equity": TOPSTEP_ACCOUNT_SIZE,
                "final_equity": TOPSTEP_ACCOUNT_SIZE}
    df = trades_df.copy()
    df["entry_dt"] = pd.to_datetime(df["entry_time"])
    df["trade_date"] = df["entry_dt"].dt.date
    df["pnl_usd"] = df["net_pnl"] * point_value
    equity = TOPSTEP_ACCOUNT_SIZE
    peak_equity = TOPSTEP_ACCOUNT_SIZE
    daily_violations = 0
    account_terminal = False
    terminal_trade = -1
    for date, day_group in df.groupby("trade_date"):
        daily_pnl = 0.0
        for idx, row in day_group.iterrows():
            if account_terminal:
                break
            trade_pnl = row["pnl_usd"]
            equity += trade_pnl
            daily_pnl += trade_pnl
            if equity > peak_equity:
                peak_equity = equity
            if peak_equity - equity >= TOPSTEP_MAX_TRAIL_DD:
                account_terminal = True
                terminal_trade = int(idx) if hasattr(idx, '__index__') else -1
        if account_terminal:
            break
        if daily_pnl < -TOPSTEP_MAX_DAILY_LOSS:
            daily_violations += 1
    return {
        "daily_violations": daily_violations,
        "account_terminal": account_terminal,
        "terminal_trade":   terminal_trade,
        "topstep_verdict":  "FAIL" if account_terminal else "PASS",
        "peak_equity":      peak_equity,
        "final_equity":     equity,
    }


def transform_double_cost(trades_df):
    return (trades_df["gross_pnl"] - 2 * trades_df["cost_pts"]).values

def transform_half_size(trades_df):
    return (trades_df["net_pnl"] * 0.5).values

def transform_slippage_shock(trades_df, tick_size, seed=42):
    rng = np.random.RandomState(seed)
    shock_pts = rng.exponential(scale=1.5 * tick_size, size=len(trades_df))
    return (trades_df["net_pnl"].values - shock_pts)

def transform_missed_20pct(trades_df, seed=42):
    rng = np.random.RandomState(seed)
    mask = rng.random(len(trades_df)) >= 0.20
    return trades_df["net_pnl"].values[mask]

def transform_stop_200(trades_df, point_value):
    cap_pts = 200.0 / point_value
    net = trades_df["net_pnl"].values.copy()
    return np.where(net < -cap_pts, -cap_pts, net)


def run_survivor(key: str, dsr_s1: float, pf_s1: float, n_s1: int) -> Optional[dict]:
    entry = get_by_key(key)
    if entry is None:
        logger.error(f"Registry key not found: {key}")
        return None

    instrument = INSTRUMENTS.get(entry.instrument)
    if instrument is None:
        logger.error(f"Instrument {entry.instrument} not in INSTRUMENTS")
        return None

    point_value = instrument.point_value
    tick_size   = instrument.tick_size

    logger.info(f"\n{'='*65}")
    logger.info(f"  Survivor: {key}  ({entry.instrument})  step1_DSR={dsr_s1:+.3f}")
    logger.info(f"{'='*65}")

    project_root = THIS_DIR.parent
    raw_dir = project_root / "01_data" / "raw"
    from src.data.data_schema import DATA_PATHS
    fname = DATA_PATHS.get(entry.data_path_key)
    if fname is None:
        logger.error(f"No DATA_PATHS entry for {entry.data_path_key}")
        return None

    _, data = load_data_cached(str(raw_dir / fname), entry.timeframe, instrument=entry.instrument)
    cost_model = build_cost_model(entry.instrument, "realistic")

    t0 = time.time()
    if entry.test_method == TestMethod.WALK_FORWARD:
        result = run_walk_forward(entry, data, cost_model)
    else:
        result = run_one_shot_is_oos(entry, data, cost_model)
    logger.info(f"  WFO done in {time.time()-t0:.1f}s")

    trades_df = result.combined_oos_trades
    if trades_df is None or len(trades_df) == 0:
        logger.error(f"  No OOS trades for {key}")
        return None

    if "net_pnl" not in trades_df.columns or "cost_pts" not in trades_df.columns:
        logger.error(f"  Trades missing net_pnl/cost_pts columns for {key}")
        return None

    n_trials = result.total_param_combos
    entry_times = pd.to_datetime(trades_df["entry_time"])
    n_trades = len(trades_df)
    span_days = max((entry_times.max() - entry_times.min()).days, 1) if n_trades > 1 else 365
    tpy = n_trades / (span_days / 365.25)
    logger.info(f"  n_trades={n_trades}, tpy={tpy:.1f}, n_trials={n_trials}")

    def m(arr):
        return _metrics(arr, n_trials, point_value, tpy)

    base_net  = trades_df["net_pnl"].values
    dbl_net   = transform_double_cost(trades_df)
    half_net  = transform_half_size(trades_df)
    shock_net = transform_slippage_shock(trades_df, tick_size)
    miss_net  = transform_missed_20pct(trades_df)
    stop_net  = transform_stop_200(trades_df, point_value)

    m_base  = m(base_net)
    m_dbl   = m(dbl_net)
    m_half  = m(half_net)
    m_shock = m(shock_net)
    m_miss  = m(miss_net)
    m_stop  = m(stop_net)
    topstep = simulate_topstep(trades_df, point_value)

    return {
        "key": key,
        "instrument": entry.instrument,
        "point_value": point_value,
        "tick_size": tick_size,
        "n_trials": n_trials,
        "tpy": tpy,
        "dsr_s1": dsr_s1,
        "pf_s1": pf_s1,
        "n_s1": n_s1,
        "base":        m_base,
        "double_cost": m_dbl,
        "half_size":   m_half,
        "slip_shock":  m_shock,
        "missed_20":   m_miss,
        "stop_200":    m_stop,
        "topstep":     topstep,
    }


def print_report(all_results: List[dict]):
    W = 115
    TESTS = ["base", "double_cost", "half_size", "slip_shock", "missed_20", "stop_200"]
    LABELS = {
        "base":        "base(real)",
        "double_cost": "2x_cost",
        "half_size":   "half_sz",
        "slip_shock":  "slip_shk",
        "missed_20":   "miss_20%",
        "stop_200":    "stop$200",
    }
    print("\n" + "=" * W)
    print("  BATCH 5 STEP 2: Extended Stress Suite — Trend Following GC Survivors")
    print("  Tests: 2x_cost | half_sz | slip_shk | miss_20% | stop$200 | topstep")
    print("  Criteria: DSR>=1.0 | PF>=1.25 | DD<=$2,000 | p<=0.05 | both_halves | mean_pnl>0")
    print("=" * W)

    overall_pass = []
    for r in all_results:
        key = r["key"]
        instr = r["instrument"]
        print(f"\n  --- {key} ({instr}) ---")
        print(f"  {'Test':<12} {'DSR':>7} {'PF':>7} {'DD($)':>9} {'n':>6} {'p':>8} {'BH':>4} {'Verdict':<8} Failures")
        print(f"  {'-'*90}")
        all_pass = True
        for t in TESTS:
            mv = r[t]
            label = LABELS[t]
            bh_str = "Y" if mv["both_halves"] else "N"
            fails = "|".join(mv["failures"]) if mv["failures"] else "-"
            v = mv["verdict"]
            if v != "PASS":
                all_pass = False
            print(f"  {label:<12} {mv['dsr']:>+7.3f} {mv['pf']:>7.3f} {mv['dd_usd']:>9,.0f}"
                  f" {mv['n']:>6} {mv['p']:>8.4f} {bh_str:>4}  {v:<8} {fails}")
        ts = r["topstep"]
        ts_v = ts["topstep_verdict"]
        if ts_v != "PASS":
            all_pass = False
        print(f"  {'topstep':<12} {'n/a':>7} {'n/a':>7} {'n/a':>9}"
              f" {r['n_s1']:>6} {'n/a':>8} {'n/a':>4}  {ts_v:<8}"
              f" daily_viol={ts['daily_violations']} terminal={ts['account_terminal']}"
              f" final_eq=${ts['final_equity']:,.0f}")
        overall_label = "ALL PASS" if all_pass else "SOME FAIL"
        overall_pass.append((key, all_pass))
        print(f"\n  >> {key}: {overall_label}")

    print("\n" + "=" * W)
    print("  BATCH 5 STEP 2 OVERALL SUMMARY")
    print("=" * W)
    all_clear = [k for k, p in overall_pass if p]
    some_fail = [k for k, p in overall_pass if not p]
    print(f"  ALL-CLEAR ({len(all_clear)}): {', '.join(all_clear) or 'none'}")
    print(f"  CONDITIONAL ({len(some_fail)}): {', '.join(some_fail) or 'none'}")
    print("=" * W)


def main():
    from datetime import datetime
    logger.info("="*65)
    logger.info("  BATCH 5 STEP 2: EXTENDED SURVIVOR STRESS SUITE")
    logger.info(f"  Started: {datetime.now().strftime('%H:%M:%S')}")
    logger.info("="*65)

    all_results = []
    for key, dsr_s1, pf_s1, n_s1 in HARDENED:
        r = run_survivor(key, dsr_s1, pf_s1, n_s1)
        if r is not None:
            all_results.append(r)

    if not all_results:
        logger.error("No results produced. Check registry and data paths.")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in all_results:
            record = {
                "schema": "stress_step2_batch5",
                "timestamp": datetime.now().isoformat(),
                "key": r["key"],
                "instrument": r["instrument"],
                "step": 2,
                "tests": {
                    t: {
                        "dsr": r[t]["dsr"],
                        "pf":  r[t]["pf"],
                        "dd_usd": r[t]["dd_usd"],
                        "n":   r[t]["n"],
                        "p":   r[t]["p"],
                        "verdict":  r[t]["verdict"],
                        "failures": r[t]["failures"],
                    }
                    for t in ["base", "double_cost", "half_size", "slip_shock", "missed_20", "stop_200"]
                },
                "topstep": r["topstep"],
            }
            f.write(json.dumps(record, default=str) + "\n")

    logger.info(f"\nResults written to: {OUT}")
    print_report(all_results)


if __name__ == "__main__":
    main()
