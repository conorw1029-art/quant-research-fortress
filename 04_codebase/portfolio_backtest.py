"""
Phase 4: Portfolio Backtest — All 8 Hardened Survivors
=======================================================
Combines all 8 confirmed ALL-CLEAR survivors into a single portfolio.
Re-runs WFO for each to get OOS trades, then reports:

  1. Per-strategy dollar P&L summary (unit sizing: 1 contract)
  2. Correlation matrix of strategy daily returns
  3. Combined portfolio equity curve stats
  4. Portfolio-level DSR, Sharpe, max drawdown
  5. Topstep simulation at portfolio level ($150k account)
  6. Equal-risk sizing variant (if stop_price stored in trades)

Key question: are bollinger_rsi_gc and vwap_reclaim_gc (both MGC Gold)
correlated enough to be a concentration risk?

Output: 05_backtests/portfolio_results.jsonl
"""

import sys, json, logging, time
from pathlib import Path
from datetime import date

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

from run_strategy import (load_data_cached, build_cost_model,
                           run_walk_forward, run_one_shot_is_oos)
from src.zoo.registry import get_by_key, DEFAULT_DATA_PATHS, TestMethod
from src.data.data_schema import INSTRUMENTS
from src.backtesting.metrics import performance_report, evaluate_go_nogo

# ── Survivors ─────────────────────────────────────────────────────────────────
# 8 confirmed ALL-CLEAR survivors (Step 1 + Step 2 complete as of 2026-05-15)
SURVIVORS = [
    # Original batch (Sessions 1-3)
    "bollinger_rsi_gc",
    "donchian_breakout_cl",
    "fomc_drift",
    "vwap_reclaim_gc",
    "vwap_reclaim_si",
    # Batch 5: Trend Following
    "vol_adj_momentum_gc",
    "donchian_intraday_gc",
    # Batch 6: RTH ORB
    "rth_orb_gc",
]

# Portfolio-level Topstep params (scaled for 8-strategy account)
PORT_ACCOUNT_SIZE    = 150_000.0
PORT_DAILY_LOSS_LIM  =   4_500.0   # 3% of account
PORT_TRAIL_DD        =   7_500.0   # 5% of account

OUTPUT = THIS_DIR.parent / "05_backtests" / "portfolio_results.jsonl"
RAW_DIR = THIS_DIR.parent / "01_data" / "raw"


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_survivor(key: str) -> dict:
    """Run WFO for one survivor, return trades + metadata."""
    entry = get_by_key(key)
    if entry is None:
        raise ValueError(f"Unknown key: {key}")

    fname = DEFAULT_DATA_PATHS.get(entry.data_path_key)
    if fname is None:
        raise ValueError(f"No data path for {entry.data_path_key}")
    csv_path = str(RAW_DIR.parent / fname)

    _, data    = load_data_cached(csv_path, entry.timeframe, instrument=entry.instrument)
    cost_model = build_cost_model(entry.instrument, "realistic")
    instr      = INSTRUMENTS[entry.instrument]

    t0 = time.time()
    if entry.test_method == TestMethod.ONE_SHOT_IS_OOS:
        result = run_one_shot_is_oos(entry, data, cost_model)
    else:
        result = run_walk_forward(entry, data, cost_model)
    elapsed = time.time() - t0
    logger.info(f"  {key}: WFO done in {elapsed:.1f}s  n={len(result.combined_oos_trades)}")

    trades = result.combined_oos_trades.copy()
    if trades is None or len(trades) == 0:
        raise ValueError(f"No OOS trades for {key}")

    # Ensure datetimes
    trades["entry_time"] = pd.to_datetime(trades["entry_time"])
    trades["exit_time"]  = pd.to_datetime(trades["exit_time"])
    trades["trade_date"] = trades["entry_time"].dt.date

    # Dollar P&L (unit sizing: 1 contract)
    trades["dollar_pnl"] = trades["net_pnl"] * instr.point_value

    # Equal-risk sizing: target $500 risk per trade, using stop distance
    if "stop_price" in trades.columns and "entry_price" in trades.columns:
        stop_dist_pts = (trades["entry_price"] - trades["stop_price"]).abs()
        stop_dist_usd = stop_dist_pts * instr.point_value
        # Avoid div by zero; clamp minimum to 1 tick
        stop_dist_usd = stop_dist_usd.clip(lower=instr.tick_size * instr.point_value)
        trades["er_size"]      = (500.0 / stop_dist_usd).clip(upper=10.0)
        trades["er_dollar_pnl"] = trades["dollar_pnl"] * trades["er_size"]
        has_er = True
    else:
        trades["er_size"]       = 1.0
        trades["er_dollar_pnl"] = trades["dollar_pnl"]
        has_er = False

    return {
        "key":         key,
        "instrument":  entry.instrument,
        "point_value": instr.point_value,
        "trades":      trades,
        "n_trials":    result.total_param_combos,
        "n_folds":     len(result.folds),
        "has_er":      has_er,
    }


def daily_pnl_series(trades: pd.DataFrame, pnl_col: str = "dollar_pnl") -> pd.Series:
    """Sum P&L by trade_date into a Series indexed by date."""
    return trades.groupby("trade_date")[pnl_col].sum()


def metrics_summary(pnl_arr: np.ndarray, key: str, n_trials: int,
                    point_value: float) -> dict:
    if len(pnl_arr) < 10:
        return {"key": key, "n": len(pnl_arr), "dsr": 0.0, "pf": 0.0,
                "sharpe": 0.0, "max_dd_usd": 0.0, "verdict": "FAIL"}
    report = performance_report(pnl_arr, trades_per_year=252.0,
                                n_trials=n_trials, instrument_point_value=point_value)
    gng = evaluate_go_nogo(report)
    s   = report["standard"]
    d   = report["dsr"]
    return {
        "key":        key,
        "n":          s["n_trades"],
        "dsr":        round(d["dsr"], 3),
        "pf":         round(s["profit_factor"], 3),
        "sharpe":     round(d["observed_sr"], 3),
        "max_dd_usd": round(s["max_drawdown_abs"] * point_value, 0),
        "mean_usd":   round(s["mean_pnl"] * point_value, 2),
        "verdict":    gng["verdict"],
        "failures":   gng["failures"],
    }


def portfolio_topstep(daily_port: pd.Series,
                      account_size: float, daily_limit: float, trail_dd: float) -> dict:
    """Simulate Topstep-style rules on combined daily portfolio P&L."""
    equity      = account_size
    peak_equity = account_size
    daily_viol  = 0
    dead        = False
    dead_date   = None

    for dt, pnl in daily_port.sort_index().items():
        if pnl < -daily_limit:
            daily_viol += 1
        equity += pnl
        if equity > peak_equity:
            peak_equity = equity
        if peak_equity - equity >= trail_dd:
            dead      = True
            dead_date = dt
            break

    return {
        "pass":             not dead,
        "terminal":         dead,
        "dead_date":        str(dead_date) if dead_date else None,
        "daily_violations": daily_viol,
        "final_equity":     round(equity, 0),
        "peak_equity":      round(peak_equity, 0),
    }


def correlation_report(survivor_data: list) -> pd.DataFrame:
    """Build correlation matrix of daily dollar P&L across strategies."""
    daily_series = {}
    for s in survivor_data:
        ds = daily_pnl_series(s["trades"], "dollar_pnl")
        daily_series[s["key"]] = ds

    df = pd.DataFrame(daily_series)
    df = df.fillna(0.0)
    return df.corr()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 70)
    logger.info("  PHASE 4: PORTFOLIO BACKTEST — 8 SURVIVORS")
    logger.info("  bollinger_rsi_gc, donchian_breakout_cl, fomc_drift,")
    logger.info("  vwap_reclaim_gc, vwap_reclaim_si, vol_adj_momentum_gc,")
    logger.info("  donchian_intraday_gc, rth_orb_gc")
    logger.info("=" * 70)

    # ── Step 1: Load all survivors ────────────────────────────────────────────
    survivor_data = []
    for key in SURVIVORS:
        logger.info(f"\nLoading {key}...")
        try:
            s = load_survivor(key)
            survivor_data.append(s)
        except Exception as e:
            logger.error(f"  FAILED to load {key}: {e}")

    if len(survivor_data) < 2:
        logger.error("Not enough survivors loaded. Aborting.")
        return

    # ── Step 2: Per-strategy unit-sizing dollar summary ───────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  INDIVIDUAL STRATEGY DOLLAR SUMMARY (1 contract, realistic costs)")
    logger.info("=" * 70)
    logger.info(f"  {'Strategy':25s} {'Instr':6s} {'n':>6} {'DSR':>8} {'PF':>6} "
                f"{'Sharpe':>8} {'Mean$/tr':>9} {'MaxDD$':>10} Verdict")
    logger.info("  " + "-" * 90)

    strategy_metrics = []
    for s in survivor_data:
        pnl_arr = s["trades"]["dollar_pnl"].values
        m = metrics_summary(pnl_arr, s["key"], s["n_trials"], s["point_value"])
        strategy_metrics.append(m)
        fail_str = ", ".join(m["failures"]) if m["failures"] else "-"
        logger.info(f"  {m['key']:25s} {s['instrument']:6s} {m['n']:6d} {m['dsr']:+8.3f} "
                    f"{m['pf']:6.3f} {m['sharpe']:+8.3f} {m['mean_usd']:+9.2f} "
                    f"{m['max_dd_usd']:>10,.0f} {m['verdict']}  {fail_str}")

    # ── Step 3: Correlation matrix ────────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  DAILY RETURN CORRELATION MATRIX (dollar P&L, unit sizing)")
    logger.info("=" * 70)

    corr = correlation_report(survivor_data)
    logger.info(f"\n  {corr.to_string()}")

    # Highlight Gold-Gold correlations (5 Gold strategies in 8-survivor set)
    gc_keys = [s["key"] for s in survivor_data if s["instrument"] == "MGC"]
    if len(gc_keys) >= 2:
        logger.info(f"\n  Gold strategy correlations ({len(gc_keys)} strategies on MGC):")
        high_corr_pairs = []
        for i, k1 in enumerate(gc_keys):
            for k2 in gc_keys[i+1:]:
                r = corr.loc[k1, k2]
                flag = "*** HIGH ***" if abs(r) > 0.5 else ("** MOD **" if abs(r) > 0.25 else "OK")
                logger.info(f"    {k1} vs {k2}: {r:+.3f}  {flag}")
                if abs(r) > 0.5:
                    high_corr_pairs.append((k1, k2, r))
        if high_corr_pairs:
            logger.info("  *** CONCENTRATION RISK — multiple high-correlation Gold strategies ***")

    # ── Step 4: Combined portfolio metrics ────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  COMBINED PORTFOLIO (unit sizing, all trades merged)")
    logger.info("=" * 70)

    all_trades = pd.concat(
        [s["trades"][["entry_time", "trade_date", "dollar_pnl", "er_dollar_pnl",
                       "direction"]].assign(strategy=s["key"])
         for s in survivor_data],
        ignore_index=True
    ).sort_values("entry_time")

    # Portfolio dollar P&L array (trade-by-trade, sorted by entry time)
    port_pnl_arr = all_trades["dollar_pnl"].values
    total_n      = len(port_pnl_arr)
    total_trials = sum(s["n_trials"] for s in survivor_data)

    port_m = metrics_summary(port_pnl_arr, "portfolio", total_trials, point_value=1.0)
    logger.info(f"  Total trades:   {total_n:,}")
    logger.info(f"  Portfolio DSR:  {port_m['dsr']:+.3f}")
    logger.info(f"  Portfolio PF:   {port_m['pf']:.3f}")
    logger.info(f"  Portfolio Sharpe (ann): {port_m['sharpe']:+.3f}")
    logger.info(f"  Max drawdown:   ${port_m['max_dd_usd']:,.0f}")
    logger.info(f"  Mean $/trade:   ${port_m['mean_usd']:+.2f}")
    logger.info(f"  Verdict:        {port_m['verdict']}  {port_m['failures']}")

    # Equity curve stats
    cum_pnl = np.cumsum(port_pnl_arr)
    total_profit = cum_pnl[-1]
    logger.info(f"  Total P&L:      ${total_profit:+,.0f}  (unit sizing, ~{len(survivor_data)} yrs OOS)")

    # ── Step 5: Daily portfolio P&L ───────────────────────────────────────────
    daily_port = all_trades.groupby("trade_date")["dollar_pnl"].sum()
    daily_port_er = all_trades.groupby("trade_date")["er_dollar_pnl"].sum()

    logger.info(f"\n  Trading days with portfolio activity: {len(daily_port):,}")
    logger.info(f"  Mean daily P&L (unit):  ${daily_port.mean():+.2f}")
    logger.info(f"  Std  daily P&L (unit):  ${daily_port.std():,.2f}")
    logger.info(f"  Daily Sharpe (unit):    {(daily_port.mean()/daily_port.std()*np.sqrt(252)):+.3f}")

    # Best/worst days
    best_day  = daily_port.idxmax()
    worst_day = daily_port.idxmin()
    logger.info(f"  Best day:   {best_day}  ${daily_port[best_day]:+,.0f}")
    logger.info(f"  Worst day:  {worst_day}  ${daily_port[worst_day]:+,.0f}")

    # Days with multiple strategies active
    trades_per_day = all_trades.groupby("trade_date")["strategy"].nunique()
    multi_strat_days = (trades_per_day > 1).sum()
    logger.info(f"  Days with 2+ strategies active: {multi_strat_days} "
                f"({100*multi_strat_days/len(daily_port):.1f}% of active days)")

    # ── Step 6: Topstep portfolio simulation ──────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info(f"  TOPSTEP PORTFOLIO SIMULATION")
    logger.info(f"  Account: ${PORT_ACCOUNT_SIZE:,.0f}  Daily limit: ${PORT_DAILY_LOSS_LIM:,.0f}"
                f"  Trail DD: ${PORT_TRAIL_DD:,.0f}")
    logger.info("=" * 70)

    ts = portfolio_topstep(daily_port, PORT_ACCOUNT_SIZE, PORT_DAILY_LOSS_LIM, PORT_TRAIL_DD)
    ts_verdict = "PASS" if ts["pass"] else "FAIL"
    logger.info(f"  Result:          {ts_verdict}")
    logger.info(f"  Daily violations:{ts['daily_violations']}")
    logger.info(f"  Terminal:        {ts['terminal']}")
    if ts["dead_date"]:
        logger.info(f"  Blew up on:      {ts['dead_date']}")
    logger.info(f"  Final equity:    ${ts['final_equity']:,.0f}")
    logger.info(f"  Peak equity:     ${ts['peak_equity']:,.0f}")

    # ── Step 7: Equal-risk sizing portfolio ───────────────────────────────────
    if any(s["has_er"] for s in survivor_data):
        logger.info("\n" + "=" * 70)
        logger.info("  EQUAL-RISK SIZING ($500/trade risk target)")
        logger.info("=" * 70)
        er_pnl = all_trades["er_dollar_pnl"].values
        er_m = metrics_summary(er_pnl, "portfolio_ER", total_trials, point_value=1.0)
        logger.info(f"  Total trades: {len(er_pnl):,}")
        logger.info(f"  DSR:   {er_m['dsr']:+.3f}")
        logger.info(f"  PF:    {er_m['pf']:.3f}")
        logger.info(f"  Total P&L: ${np.sum(er_pnl):+,.0f}")
        logger.info(f"  Max DD:    ${er_m['max_dd_usd']:,.0f}")
        logger.info(f"  Verdict:   {er_m['verdict']}")

        ts_er = portfolio_topstep(daily_port_er, PORT_ACCOUNT_SIZE,
                                  PORT_DAILY_LOSS_LIM, PORT_TRAIL_DD)
        logger.info(f"  Topstep (ER): {'PASS' if ts_er['pass'] else 'FAIL'}  "
                    f"final_eq=${ts_er['final_equity']:,.0f}  "
                    f"daily_viol={ts_er['daily_violations']}")

    # ── Step 8: Strategy overlap analysis ────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  STRATEGY OVERLAP ANALYSIS")
    logger.info("=" * 70)

    pivot = (all_trades.groupby(["trade_date", "strategy"])
             .size().unstack(fill_value=0))
    for col in [s["key"] for s in survivor_data]:
        if col not in pivot.columns:
            pivot[col] = 0

    for s1 in survivor_data:
        for s2 in survivor_data:
            if s2["key"] <= s1["key"]:
                continue
            k1, k2 = s1["key"], s2["key"]
            if k1 not in pivot.columns or k2 not in pivot.columns:
                continue
            both = ((pivot[k1] > 0) & (pivot[k2] > 0)).sum()
            total_k1 = (pivot[k1] > 0).sum()
            pct = 100 * both / total_k1 if total_k1 > 0 else 0
            logger.info(f"  {k1} ∩ {k2}: {both} shared days ({pct:.1f}% of {k1} days)")

    # ── Step 9: Per-year portfolio P&L ────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  ANNUAL PORTFOLIO P&L (unit sizing)")
    logger.info("=" * 70)
    all_trades["year"] = all_trades["entry_time"].dt.year
    annual = all_trades.groupby("year")["dollar_pnl"].agg(["sum", "count"])
    annual.columns = ["total_pnl", "n_trades"]
    logger.info(f"  {'Year':>6} {'P&L':>12} {'n_trades':>10}")
    logger.info("  " + "-" * 32)
    for yr, row in annual.iterrows():
        logger.info(f"  {yr:>6} ${row['total_pnl']:>+11,.0f} {int(row['n_trades']):>10}")
    positive_years = (annual["total_pnl"] > 0).sum()
    logger.info(f"\n  Positive years: {positive_years}/{len(annual)}")

    # ── Final summary ─────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  PORTFOLIO VERDICT")
    logger.info("=" * 70)
    logger.info(f"  Individual survivors:   {len(survivor_data)}/8 loaded")
    logger.info(f"  Portfolio DSR:          {port_m['dsr']:+.3f}  ({'STRONG' if port_m['dsr'] > 5 else 'MODERATE' if port_m['dsr'] > 2 else 'WEAK'})")
    logger.info(f"  Portfolio Topstep:      {'PASS' if ts['pass'] else 'FAIL'}")
    logger.info(f"  Positive years:         {positive_years}/{len(annual)}")
    if len(gc_keys) >= 2:
        max_gc_corr = max(corr.loc[k1, k2] for i, k1 in enumerate(gc_keys) for k2 in gc_keys[i+1:])
        logger.info(f"  Max Gold-Gold corr:     {max_gc_corr:+.3f}  ({'RISK' if max_gc_corr > 0.5 else 'OK'})")

    overall = "PROCEED_TO_LIVE" if (
        port_m["dsr"] > 2.0 and ts["pass"] and positive_years >= len(annual) * 0.7
    ) else "NEEDS_REVIEW"
    logger.info(f"\n  >> PORTFOLIO STATUS: {overall}")
    logger.info("=" * 70)

    # ── Write results ─────────────────────────────────────────────────────────
    output_record = {
        "timestamp":          str(date.today()),
        "n_survivors":        len(survivor_data),
        "survivor_keys":      [s["key"] for s in survivor_data],
        "strategy_metrics":   strategy_metrics,
        "portfolio_unit":     port_m,
        "portfolio_topstep":  ts,
        "annual_pnl":         annual.reset_index().to_dict("records"),
        "correlation":        corr.to_dict(),
        "overlap_days":       {},
        "overall":            overall,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "a") as f:
        f.write(json.dumps(output_record, default=str) + "\n")
    logger.info(f"\nResults written to: {OUTPUT}")


if __name__ == "__main__":
    main()
