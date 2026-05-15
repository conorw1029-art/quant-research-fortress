"""
Zoo Pending Investigation — Two unresolved zoo strategies
=========================================================

1. fomc_drift_zn (ZN): Zoo PASS at DSR=1.107, PF=2.055, n=57.
   No Step 2 stress ever run. Borderline DSR. Running full stress suite.

2. bollinger_rsi_fxe (M6E): Zoo PASS at DSR=11.254, PF=1.465, n=9453 —
   BUT tested at ZERO cost (TransactionCost 0.000pts/RT). Must re-run
   at realistic M6E costs to get a valid verdict.

Output: 05_backtests/zoo_pending_results.jsonl
"""

import sys, json, logging, time
from pathlib import Path
from typing import Optional

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
from src.data.data_schema import INSTRUMENTS, DATA_PATHS
from src.backtesting.metrics import evaluate_go_nogo, performance_report

TOPSTEP_ACCOUNT_SIZE   = 25_000.0
TOPSTEP_MAX_DAILY_LOSS =  1_500.0
TOPSTEP_MAX_TRAIL_DD   =  2_000.0

OUT = THIS_DIR.parent / "05_backtests" / "zoo_pending_results.jsonl"


# ── Metric helpers ────────────────────────────────────────────────────────────

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
                "topstep_verdict": "FAIL",
                "peak_equity": TOPSTEP_ACCOUNT_SIZE,
                "final_equity": TOPSTEP_ACCOUNT_SIZE}
    df = trades_df.copy()
    df["entry_dt"]  = pd.to_datetime(df["entry_time"])
    df["trade_date"] = df["entry_dt"].dt.date
    df["pnl_usd"]   = df["net_pnl"] * point_value
    equity = TOPSTEP_ACCOUNT_SIZE
    peak_equity = TOPSTEP_ACCOUNT_SIZE
    daily_violations = 0
    account_terminal = False
    for date, day_group in df.groupby("trade_date"):
        daily_pnl = 0.0
        for _, row in day_group.iterrows():
            if account_terminal:
                break
            equity += row["pnl_usd"]
            daily_pnl += row["pnl_usd"]
            if equity > peak_equity:
                peak_equity = equity
            if peak_equity - equity >= TOPSTEP_MAX_TRAIL_DD:
                account_terminal = True
        if account_terminal:
            break
        if daily_pnl < -TOPSTEP_MAX_DAILY_LOSS:
            daily_violations += 1
    return {
        "daily_violations": daily_violations,
        "account_terminal": account_terminal,
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
    return trades_df["net_pnl"].values - shock_pts

def transform_missed_20pct(trades_df, seed=42):
    rng = np.random.RandomState(seed)
    mask = rng.random(len(trades_df)) >= 0.20
    return trades_df["net_pnl"].values[mask]

def transform_stop_200(trades_df, point_value):
    cap_pts = 200.0 / point_value
    net = trades_df["net_pnl"].values.copy()
    return np.where(net < -cap_pts, -cap_pts, net)


# ── Core runner ───────────────────────────────────────────────────────────────

def run_stress(key: str, dsr_s1: float, pf_s1: float, n_s1: int,
               cost_scenario: str = "realistic", label: str = "") -> Optional[dict]:
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
    logger.info(f"  {label or key}  ({entry.instrument})  zoo_DSR={dsr_s1:+.3f}")
    logger.info(f"  Cost scenario: {cost_scenario}")
    logger.info(f"{'='*65}")

    raw_dir = THIS_DIR.parent / "01_data" / "raw"
    fname = DATA_PATHS.get(entry.data_path_key)
    if fname is None:
        logger.error(f"No DATA_PATHS entry for {entry.data_path_key}")
        return None

    _, data = load_data_cached(str(raw_dir / fname), entry.timeframe,
                               instrument=entry.instrument)
    cost_model = build_cost_model(entry.instrument, cost_scenario)

    t0 = time.time()
    if entry.test_method == TestMethod.ONE_SHOT_IS_OOS:
        result = run_one_shot_is_oos(entry, data, cost_model)
    else:
        result = run_walk_forward(entry, data, cost_model)
    logger.info(f"  WFO done in {time.time()-t0:.1f}s")

    trades_df = result.combined_oos_trades
    if trades_df is None or len(trades_df) == 0:
        logger.error(f"  No OOS trades for {key}")
        return None

    if "net_pnl" not in trades_df.columns or "cost_pts" not in trades_df.columns:
        logger.error(f"  Trades missing net_pnl/cost_pts for {key}")
        return None

    n_trials = result.total_param_combos
    entry_times = pd.to_datetime(trades_df["entry_time"])
    n_trades = len(trades_df)
    span_days = max((entry_times.max() - entry_times.min()).days, 1)
    tpy = n_trades / (span_days / 365.25)
    logger.info(f"  n_trades={n_trades}, tpy={tpy:.1f}, n_trials={n_trials}")
    logger.info(f"  Cost/RT: {cost_model.cost_per_rt():.4f} pts")

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

    TESTS = ["base", "double_cost", "half_size", "slip_shock", "missed_20", "stop_200"]
    LABELS = {"base": "base(real)", "double_cost": "2x_cost", "half_size": "half_sz",
              "slip_shock": "slip_shk", "missed_20": "miss_20%", "stop_200": "stop$200"}
    results_map = {"base": m_base, "double_cost": m_dbl, "half_size": m_half,
                   "slip_shock": m_shock, "missed_20": m_miss, "stop_200": m_stop}

    all_pass = True
    logger.info(f"\n  {'Test':<12} {'DSR':>7} {'PF':>7} {'DD($)':>9} {'n':>6} {'Verdict':<8} Failures")
    logger.info(f"  {'-'*75}")
    for t in TESTS:
        mv = results_map[t]
        if mv["verdict"] != "PASS":
            all_pass = False
        fails = "|".join(mv["failures"]) if mv["failures"] else "-"
        logger.info(f"  {LABELS[t]:<12} {mv['dsr']:>+7.3f} {mv['pf']:>7.3f}"
                    f" {mv['dd_usd']:>9,.0f} {mv['n']:>6}  {mv['verdict']:<8} {fails}")

    ts = topstep
    if ts["topstep_verdict"] != "PASS":
        all_pass = False
    logger.info(f"  {'topstep':<12} {'n/a':>7} {'n/a':>7} {'n/a':>9} {n_trades:>6}"
                f"  {ts['topstep_verdict']:<8}"
                f" daily_viol={ts['daily_violations']} terminal={ts['account_terminal']}"
                f" final_eq=${ts['final_equity']:,.0f}")

    overall = "ALL-CLEAR" if all_pass else "CONDITIONAL"
    logger.info(f"\n  >> {key}: {overall}")

    return {
        "key":          key,
        "label":        label,
        "instrument":   entry.instrument,
        "cost_scenario": cost_scenario,
        "point_value":  point_value,
        "tick_size":    tick_size,
        "cost_per_rt":  cost_model.cost_per_rt(),
        "n_trials":     n_trials,
        "tpy":          tpy,
        "dsr_zoo":      dsr_s1,
        "pf_zoo":       pf_s1,
        "n_zoo":        n_s1,
        "base":         m_base,
        "double_cost":  m_dbl,
        "half_size":    m_half,
        "slip_shock":   m_shock,
        "missed_20":    m_miss,
        "stop_200":     m_stop,
        "topstep":      topstep,
        "overall":      overall,
    }


def main():
    from datetime import datetime
    logger.info("="*65)
    logger.info("  ZOO PENDING INVESTIGATION")
    logger.info("  1. fomc_drift_zn  — Step 2 stress (borderline DSR=1.107)")
    logger.info("  2. bollinger_rsi_fxe — Re-run at realistic M6E costs")
    logger.info(f"  Started: {datetime.now().strftime('%H:%M:%S')}")
    logger.info("="*65)

    all_results = []

    # ── 1. fomc_drift_zn: Step 2 full stress ─────────────────────────────────
    logger.info("\n\n>>> INVESTIGATION 1: fomc_drift_zn")
    logger.info("    Zoo record: DSR=1.107, PF=2.055, n=57, realistic costs")
    logger.info("    Status: Zoo PASS but no Step 2 ever run. n=57 is very low.")
    r1 = run_stress(
        key="fomc_drift_zn",
        dsr_s1=1.107, pf_s1=2.055, n_s1=57,
        cost_scenario="realistic",
        label="fomc_drift_zn — Step 2 stress",
    )
    if r1:
        all_results.append(r1)

    # ── 2. bollinger_rsi_fxe: Re-run at realistic costs ──────────────────────
    logger.info("\n\n>>> INVESTIGATION 2: bollinger_rsi_fxe")
    logger.info("    Zoo record: DSR=11.254, PF=1.465, n=9453 — AT ZERO COST")
    logger.info("    Re-running at realistic M6E costs to get valid verdict.")
    r2 = run_stress(
        key="bollinger_rsi_fxe",
        dsr_s1=11.254, pf_s1=1.465, n_s1=9453,
        cost_scenario="realistic",
        label="bollinger_rsi_fxe — realistic cost re-run",
    )
    if r2:
        all_results.append(r2)

    # ── Write results ─────────────────────────────────────────────────────────
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        for r in all_results:
            f.write(json.dumps(r, default=str) + "\n")

    logger.info(f"\n\nResults written to: {OUT}")
    logger.info("\n" + "="*65)
    logger.info("  ZOO PENDING — FINAL VERDICTS")
    logger.info("="*65)
    for r in all_results:
        logger.info(f"  {r['key']:35s}  {r['overall']}")
        logger.info(f"    base DSR={r['base']['dsr']:+.3f}  cost/RT={r['cost_per_rt']:.4f}pts")
    logger.info("="*65)


if __name__ == "__main__":
    main()
