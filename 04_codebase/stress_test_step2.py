"""
STEP 2: Extended Survivor Stress Test Suite
============================================
Runs the 3 hardened survivors (post-conservative-cost Step 1) through
five additional stress regimes to confirm robustness before paper trading.

TESTS:
  1. double_cost   — charge 2x the realistic per-trade cost (~4 ticks/side)
  2. half_size     — halve position size (reports absolute P&L and drawdown)
  3. slip_shock    — add fat-tail random slippage per trade (exponential dist)
  4. missed_20     — randomly drop 20% of signals (execution gaps)
  5. stop_200      — apply hard $200 per-trade stop (prop firm sizing constraint)
  6. topstep       — simulate Topstep Funded rules: $1500/day limit, $2000 trailing DD

All tests use per-trade data from a fresh realistic-cost WFO run.
Results written to 05_backtests/zoo_stress_step2.jsonl.
"""

import sys, json, logging, time
from pathlib import Path
from typing import Dict, List, Tuple, Optional

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
from src.backtesting.metrics import standard_metrics, deflated_sharpe_ratio, evaluate_go_nogo, performance_report

# ── Go/no-go thresholds ───────────────────────────────────────────
MIN_DSR   = 1.0
MIN_PF    = 1.25
MAX_P     = 0.05
MAX_DD    = 2000.0
MIN_N     = 30
DSR_WAIVER = 3.0

# ── Topstep Funded account parameters ────────────────────────────
TOPSTEP_ACCOUNT_SIZE    = 25_000.0
TOPSTEP_MAX_DAILY_LOSS  =  1_500.0   # daily loss limit
TOPSTEP_MAX_TRAIL_DD    =  2_000.0   # trailing drawdown from peak

# ── Hardened survivors from Step 1 ───────────────────────────────
# Only the 3 that passed conservative cost stress
HARDENED = [
    # key                      DSR_cons  PF_cons   n_cons
    ("bollinger_rsi_gc",        3.329,   1.408,    2220),
    ("donchian_breakout_cl",    4.445,   2.982,     236),
    ("fomc_drift",              1.544,   2.791,      57),
]

ZOO_OUT = THIS_DIR.parent / "05_backtests" / "zoo_stress_step2.jsonl"


# ══════════════════════════════════════════════════════════════════
# METRIC HELPERS
# ══════════════════════════════════════════════════════════════════

def _metrics(net_pnl: np.ndarray, n_trials: int, point_value: float,
             trades_per_year: float) -> dict:
    """Compute the subset of metrics we report in the stress table."""
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
        "both_halves": s["both_halves_positive"],
        "mean_pnl":   s["mean_pnl"],
        "verdict":    gng["verdict"],
        "failures":   gng["failures"],
    }


def _verdict_str(m: dict) -> str:
    return m["verdict"]


# ══════════════════════════════════════════════════════════════════
# TOPSTEP SIMULATION
# ══════════════════════════════════════════════════════════════════

def simulate_topstep(trades_df: pd.DataFrame, point_value: float) -> dict:
    """
    Simulate a Topstep Funded account through the OOS trade stream.

    Rules:
      - Daily loss limit: $1,500. Exceeding this is a 'daily violation'.
      - Trailing drawdown: $2,000 from peak equity.
        First breach terminates the account.

    Returns a dict with:
      - daily_violations: int (days where loss > $1,500)
      - account_terminal: bool (did trailing DD ever breach $2,000?)
      - terminal_trade: int index of first terminal breach (-1 if none)
      - topstep_verdict: "PASS" or "FAIL"
      - peak_equity: float (high-water mark reached)
      - final_equity: float
    """
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

            # Update high-water mark
            if equity > peak_equity:
                peak_equity = equity

            # Check trailing drawdown (intra-trade)
            trailing_dd = peak_equity - equity
            if trailing_dd >= TOPSTEP_MAX_TRAIL_DD:
                account_terminal = True
                terminal_trade = int(idx) if hasattr(idx, '__index__') else -1

        if account_terminal:
            break

        # End-of-day: check daily loss limit
        if daily_pnl < -TOPSTEP_MAX_DAILY_LOSS:
            daily_violations += 1

    topstep_verdict = "FAIL" if account_terminal else "PASS"

    return {
        "daily_violations": daily_violations,
        "account_terminal": account_terminal,
        "terminal_trade":   terminal_trade,
        "topstep_verdict":  topstep_verdict,
        "peak_equity":      peak_equity,
        "final_equity":     equity,
    }


# ══════════════════════════════════════════════════════════════════
# STRESS TRANSFORMS
# ══════════════════════════════════════════════════════════════════

def transform_double_cost(trades_df: pd.DataFrame) -> np.ndarray:
    """Charge 2× the realistic per-trade cost. net = gross - 2*cost."""
    return (trades_df["gross_pnl"] - 2 * trades_df["cost_pts"]).values


def transform_half_size(trades_df: pd.DataFrame) -> np.ndarray:
    """Halve position size: net = net * 0.5."""
    return (trades_df["net_pnl"] * 0.5).values


def transform_slippage_shock(trades_df: pd.DataFrame, tick_size: float,
                              seed: int = 42) -> np.ndarray:
    """
    Add fat-tail random slippage per trade drawn from Exponential(1.5 ticks).
    This models the adverse fill tail in fast markets.
    """
    rng = np.random.RandomState(seed)
    n = len(trades_df)
    shock_pts = rng.exponential(scale=1.5 * tick_size, size=n)
    return (trades_df["net_pnl"].values - shock_pts)


def transform_missed_20pct(trades_df: pd.DataFrame, seed: int = 42) -> np.ndarray:
    """Drop 20% of trades at random (models execution gaps / missed signals)."""
    rng = np.random.RandomState(seed)
    n = len(trades_df)
    mask = rng.random(n) >= 0.20   # keep 80%
    return trades_df["net_pnl"].values[mask]


def transform_stop_200(trades_df: pd.DataFrame, point_value: float) -> np.ndarray:
    """Apply a hard $200 per-trade stop loss."""
    cap_pts = 200.0 / point_value
    net = trades_df["net_pnl"].values.copy()
    net = np.where(net < -cap_pts, -cap_pts, net)
    return net


# ══════════════════════════════════════════════════════════════════
# PER-SURVIVOR RUNNER
# ══════════════════════════════════════════════════════════════════

def run_survivor(key: str, dsr_cons: float, pf_cons: float,
                 n_cons: int) -> Optional[dict]:
    """
    Re-run the survivor at realistic cost, capture per-trade data,
    apply all stress transforms, return a results dict.
    """
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
    logger.info(f"  Survivor: {key}  ({entry.instrument})  cons_DSR={dsr_cons:+.3f}")
    logger.info(f"{'='*65}")

    # Load data
    project_root = THIS_DIR.parent
    raw_dir = project_root / "01_data" / "raw"
    from src.data.data_schema import DATA_PATHS
    fname = DATA_PATHS.get(entry.data_path_key)
    if fname is None:
        logger.error(f"No DATA_PATHS entry for {entry.data_path_key}")
        return None
    csv_path = str(raw_dir / fname)

    _, data = load_data_cached(csv_path, entry.timeframe, instrument=entry.instrument)
    cost_model = build_cost_model(entry.instrument, "realistic")

    # Run WFO / one-shot at realistic cost
    t0 = time.time()
    if entry.test_method == TestMethod.WALK_FORWARD:
        result = run_walk_forward(entry, data, cost_model)
    else:
        result = run_one_shot_is_oos(entry, data, cost_model)
    elapsed = time.time() - t0
    logger.info(f"  WFO done in {elapsed:.1f}s")

    trades_df = result.combined_oos_trades
    if trades_df is None or len(trades_df) == 0:
        logger.error(f"  No OOS trades for {key}")
        return None

    if "net_pnl" not in trades_df.columns or "cost_pts" not in trades_df.columns:
        logger.error(f"  Trades missing net_pnl/cost_pts columns for {key}")
        return None

    n_trials = result.total_param_combos
    # Estimate trades per year
    entry_times = pd.to_datetime(trades_df["entry_time"])
    n_trades = len(trades_df)
    if n_trades > 1:
        span_days = max((entry_times.max() - entry_times.min()).days, 1)
        tpy = n_trades / (span_days / 365.25)
    else:
        tpy = 252.0

    logger.info(f"  n_trades={n_trades}, tpy={tpy:.1f}, n_trials={n_trials}")

    def m(arr):
        return _metrics(arr, n_trials, point_value, tpy)

    # ── Apply all transforms ──────────────────────────────────────
    base_net    = trades_df["net_pnl"].values
    dbl_net     = transform_double_cost(trades_df)
    half_net    = transform_half_size(trades_df)
    shock_net   = transform_slippage_shock(trades_df, tick_size)
    miss_net    = transform_missed_20pct(trades_df)
    stop_net    = transform_stop_200(trades_df, point_value)

    m_base  = m(base_net)
    m_dbl   = m(dbl_net)
    m_half  = m(half_net)
    m_shock = m(shock_net)
    m_miss  = m(miss_net)
    m_stop  = m(stop_net)

    # Topstep: apply to base trades (realistic cost)
    topstep = simulate_topstep(trades_df, point_value)

    return {
        "key": key,
        "instrument": entry.instrument,
        "point_value": point_value,
        "tick_size": tick_size,
        "n_trials": n_trials,
        "tpy": tpy,
        "dsr_cons": dsr_cons,
        "pf_cons": pf_cons,
        "n_cons": n_cons,
        "base":    m_base,
        "double_cost": m_dbl,
        "half_size":   m_half,
        "slip_shock":  m_shock,
        "missed_20":   m_miss,
        "stop_200":    m_stop,
        "topstep":     topstep,
    }


# ══════════════════════════════════════════════════════════════════
# REPORT
# ══════════════════════════════════════════════════════════════════

def print_report(all_results: List[dict]):
    W = 115
    TESTS = ["base", "double_cost", "half_size", "slip_shock", "missed_20", "stop_200"]
    TEST_LABELS = {
        "base":        "base(real)",
        "double_cost": "2x_cost",
        "half_size":   "half_sz",
        "slip_shock":  "slip_shk",
        "missed_20":   "miss_20%",
        "stop_200":    "stop$200",
    }

    print("\n" + "=" * W)
    print("  STEP 2: Extended Survivor Stress Suite")
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
            m = r[t]
            label = TEST_LABELS[t]
            bh_str = "Y" if m["both_halves"] else "N"
            fails = "|".join(m["failures"]) if m["failures"] else "-"
            v = m["verdict"]
            if v != "PASS":
                all_pass = False
            print(f"  {label:<12} {m['dsr']:>+7.3f} {m['pf']:>7.3f} {m['dd_usd']:>9,.0f}"
                  f" {m['n']:>6} {m['p']:>8.4f} {bh_str:>4}  {v:<8} {fails}")

        # Topstep row
        ts = r["topstep"]
        ts_v = ts["topstep_verdict"]
        if ts_v != "PASS":
            all_pass = False
        print(f"  {'topstep':<12} {'n/a':>7} {'n/a':>7} {'n/a':>9}"
              f" {r['n_cons']:>6} {'n/a':>8} {'n/a':>4}  {ts_v:<8}"
              f" daily_viol={ts['daily_violations']} terminal={ts['account_terminal']}"
              f" final_eq=${ts['final_equity']:,.0f}")

        overall_label = "ALL PASS" if all_pass else "SOME FAIL"
        overall_pass.append((key, all_pass))
        print(f"\n  >> {key}: {overall_label}")

    print("\n" + "=" * W)
    print("  STEP 2 OVERALL SUMMARY")
    print("=" * W)
    all_clear = []
    some_fail = []
    for key, passed in overall_pass:
        if passed:
            all_clear.append(key)
        else:
            some_fail.append(key)

    print(f"  ALL-CLEAR ({len(all_clear)}): {', '.join(all_clear) or 'none'}")
    print(f"  CONDITIONAL ({len(some_fail)}): {', '.join(some_fail) or 'none'}")
    print("=" * W)


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    logger.info("="*65)
    logger.info("  STEP 2: EXTENDED SURVIVOR STRESS SUITE")
    logger.info("  Hardened survivors from Step 1 (post-conservative-cost)")
    logger.info("="*65)

    all_results = []
    for key, dsr_cons, pf_cons, n_cons in HARDENED:
        r = run_survivor(key, dsr_cons, pf_cons, n_cons)
        if r is not None:
            all_results.append(r)

    if not all_results:
        logger.error("No results produced. Check registry and data paths.")
        return

    # Write summary to zoo file
    ZOO_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(ZOO_OUT, "a", encoding="utf-8") as f:
        for r in all_results:
            record = {
                "schema": "stress_step2",
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "key": r["key"],
                "instrument": r["instrument"],
                "step": 2,
                "tests": {
                    t: {
                        "dsr": r[t]["dsr"],
                        "pf": r[t]["pf"],
                        "dd_usd": r[t]["dd_usd"],
                        "n": r[t]["n"],
                        "p": r[t]["p"],
                        "verdict": r[t]["verdict"],
                        "failures": r[t]["failures"],
                    }
                    for t in ["base", "double_cost", "half_size", "slip_shock", "missed_20", "stop_200"]
                },
                "topstep": r["topstep"],
            }
            f.write(json.dumps(record, default=str) + "\n")

    logger.info(f"\nResults written to: {ZOO_OUT}")
    print_report(all_results)


if __name__ == "__main__":
    main()
