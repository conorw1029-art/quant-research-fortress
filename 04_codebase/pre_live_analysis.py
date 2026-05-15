"""
Pre-Live Portfolio Analysis — Final Gate Before Signal Delivery
===============================================================
Runs every remaining analytical check before going live:

  1. Parameter stability  — are WFO param selections stable across folds?
  2. Trade statistics     — win rate, avg win/loss, max consec losses, hold time
  3. Monthly P&L          — worst/best months, consecutive losing months
  4. Monte Carlo (10 000) — P(daily limit breach), P(trail DD breach),
                            worst-day distribution, max DD percentiles
  5. Sizing sensitivity   — daily VaR at $250 / $500 / $750 per-trade risk
  6. Drawdown recovery    — expected days to recover from historical max DD

Outputs:
  05_backtests/daily_portfolio_pnl.csv   (cached daily P&L for future use)
  05_backtests/pre_live_analysis.json
"""

import sys, json, logging, time
from pathlib import Path
from collections import Counter, defaultdict

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
from src.backtesting.metrics import performance_report

# ── Config ────────────────────────────────────────────────────────────────────

SURVIVORS = [
    "bollinger_rsi_gc",
    "donchian_breakout_cl",
    "fomc_drift",
    "vwap_reclaim_gc",
    "vwap_reclaim_si",
    "vol_adj_momentum_gc",
    "donchian_intraday_gc",
    "rth_orb_gc",
    "vol_adj_momentum_si",
]

PORT_ACCOUNT_SIZE   = 150_000.0
PORT_DAILY_LIMIT    =   4_500.0
PORT_TRAIL_DD       =   7_500.0
N_MONTE_CARLO       =  10_000
RISK_LEVELS         = [250, 500, 750]  # per-trade risk for equal-risk sizing

RAW_DIR  = THIS_DIR.parent / "01_data" / "raw"
OUT_DIR  = THIS_DIR.parent / "05_backtests"
OUT_JSON = OUT_DIR / "pre_live_analysis.json"
OUT_CSV  = OUT_DIR / "daily_portfolio_pnl.csv"


# ── Load survivors ────────────────────────────────────────────────────────────

def load_survivor(key: str) -> dict:
    entry = get_by_key(key)
    fname = DEFAULT_DATA_PATHS[entry.data_path_key]
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
    trades["entry_time"] = pd.to_datetime(trades["entry_time"])
    trades["exit_time"]  = pd.to_datetime(trades["exit_time"])
    trades["trade_date"] = trades["entry_time"].dt.date
    trades["dollar_pnl"] = trades["net_pnl"] * instr.point_value

    # Equal-risk sizing variants
    if "stop_price" in trades.columns and "entry_price" in trades.columns:
        stop_dist_pts = (trades["entry_price"] - trades["stop_price"]).abs()
        stop_dist_usd = (stop_dist_pts * instr.point_value).clip(
            lower=instr.tick_size * instr.point_value)
        trades["stop_dist_usd"] = stop_dist_usd
        has_er = True
    else:
        trades["stop_dist_usd"] = 0.0
        has_er = False

    # Param stability
    if entry.test_method == TestMethod.WALK_FORWARD and result.folds:
        stability = result.param_stability_score()
        fold_params = [f.best_params for f in result.folds]
    else:
        stability = 1.0
        fold_params = []

    return {
        "key":         key,
        "instrument":  entry.instrument,
        "point_value": instr.point_value,
        "trades":      trades,
        "n_trials":    result.total_param_combos,
        "n_folds":     len(result.folds),
        "has_er":      has_er,
        "stability":   stability,
        "fold_params": fold_params,
    }


# ── Analysis functions ────────────────────────────────────────────────────────

def trade_statistics(trades: pd.DataFrame, point_value: float) -> dict:
    pnl = trades["dollar_pnl"].values
    wins   = pnl[pnl > 0]
    losses = pnl[pnl <= 0]

    # Holding time in hours
    if "entry_time" in trades.columns and "exit_time" in trades.columns:
        hold_mins = ((trades["exit_time"] - trades["entry_time"])
                     .dt.total_seconds() / 60).values
        avg_hold_hrs = round(float(np.mean(hold_mins)) / 60, 2)
        max_hold_hrs = round(float(np.max(hold_mins)) / 60, 2)
    else:
        avg_hold_hrs = max_hold_hrs = None

    # Max consecutive losses
    consec = 0
    max_consec = 0
    for p in pnl:
        if p <= 0:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0

    return {
        "n":                    int(len(pnl)),
        "win_rate":             round(len(wins) / len(pnl) * 100, 1),
        "avg_win_usd":          round(float(np.mean(wins)), 2) if len(wins) > 0 else 0,
        "avg_loss_usd":         round(float(np.mean(losses)), 2) if len(losses) > 0 else 0,
        "payoff_ratio":         round(abs(float(np.mean(wins)) / float(np.mean(losses))), 2)
                                if len(wins) > 0 and len(losses) > 0 else None,
        "max_consec_losses":    int(max_consec),
        "avg_hold_hrs":         avg_hold_hrs,
        "max_hold_hrs":         max_hold_hrs,
        "largest_win_usd":      round(float(np.max(wins)), 2) if len(wins) > 0 else 0,
        "largest_loss_usd":     round(float(np.min(losses)), 2) if len(losses) > 0 else 0,
    }


def monthly_pnl(daily_port: pd.Series) -> dict:
    df = daily_port.reset_index()
    df.columns = ["date", "pnl"]
    df["date"] = pd.to_datetime(df["date"])
    df["ym"] = df["date"].dt.to_period("M")
    monthly = df.groupby("ym")["pnl"].sum()

    n_months = len(monthly)
    n_positive = (monthly > 0).sum()
    best  = monthly.idxmax()
    worst = monthly.idxmin()

    # Max consecutive losing months
    consec = 0
    max_consec_neg = 0
    for v in monthly.values:
        if v <= 0:
            consec += 1
            max_consec_neg = max(max_consec_neg, consec)
        else:
            consec = 0

    records = []
    for ym, val in monthly.items():
        records.append({"month": str(ym), "pnl": round(float(val), 2)})

    return {
        "n_months":              int(n_months),
        "n_profitable_months":   int(n_positive),
        "pct_profitable":        round(float(n_positive) / n_months * 100, 1),
        "best_month":            str(best),
        "best_month_pnl":        round(float(monthly[best]), 2),
        "worst_month":           str(worst),
        "worst_month_pnl":       round(float(monthly[worst]), 2),
        "max_consec_losing":     int(max_consec_neg),
        "avg_monthly_pnl":       round(float(monthly.mean()), 2),
        "monthly_records":       records,
    }


def monte_carlo(daily_pnl: np.ndarray, n_sims: int,
                account: float, daily_limit: float, trail_dd: float) -> dict:
    rng = np.random.default_rng(42)
    n_days = len(daily_pnl)

    max_trail_dds  = np.zeros(n_sims)
    worst_days     = np.zeros(n_sims)
    final_pnls     = np.zeros(n_sims)
    daily_viol_any = np.zeros(n_sims, dtype=bool)

    for i in range(n_sims):
        sim = rng.choice(daily_pnl, size=n_days, replace=True)
        equity = account + np.cumsum(sim)
        peak   = np.maximum.accumulate(equity)
        dd     = peak - equity
        max_trail_dds[i]  = dd.max()
        worst_days[i]     = sim.min()
        final_pnls[i]     = sim.sum()
        daily_viol_any[i] = (sim < -daily_limit).any()

    p_trail_dd_breach = float((max_trail_dds > trail_dd).mean())
    p_daily_viol      = float(daily_viol_any.mean())

    worst_day_pcts = {
        "p50": round(float(np.percentile(worst_days, 50)), 2),
        "p25": round(float(np.percentile(worst_days, 25)), 2),
        "p10": round(float(np.percentile(worst_days, 10)), 2),
        "p05": round(float(np.percentile(worst_days, 5)), 2),
        "p01": round(float(np.percentile(worst_days, 1)), 2),
    }
    max_dd_pcts = {
        "p50": round(float(np.percentile(max_trail_dds, 50)), 2),
        "p75": round(float(np.percentile(max_trail_dds, 75)), 2),
        "p90": round(float(np.percentile(max_trail_dds, 90)), 2),
        "p95": round(float(np.percentile(max_trail_dds, 95)), 2),
        "p99": round(float(np.percentile(max_trail_dds, 99)), 2),
    }

    return {
        "n_sims":                 n_sims,
        "n_days_per_sim":         int(n_days),
        "p_trail_dd_breach":      round(p_trail_dd_breach, 4),
        "p_daily_limit_breach":   round(p_daily_viol, 4),
        "worst_day_usd":          worst_day_pcts,
        "max_trailing_dd_usd":    max_dd_pcts,
        "median_final_pnl":       round(float(np.median(final_pnls)), 2),
        "p05_final_pnl":          round(float(np.percentile(final_pnls, 5)), 2),
        "p95_final_pnl":          round(float(np.percentile(final_pnls, 95)), 2),
    }


def sizing_sensitivity(survivor_data: list,
                       daily_limit: float, trail_dd: float) -> list:
    results = []
    for risk_per_trade in RISK_LEVELS:
        all_daily = []
        for s in survivor_data:
            t = s["trades"]
            if s["has_er"] and "stop_dist_usd" in t.columns:
                sizes = (risk_per_trade / t["stop_dist_usd"]).clip(upper=10.0)
                er_pnl = t["dollar_pnl"] * sizes
            else:
                er_pnl = t["dollar_pnl"]
            daily = er_pnl.groupby(t["trade_date"]).sum()
            all_daily.append(daily)

        port = pd.concat(all_daily).groupby(level=0).sum()
        daily_arr = port.values

        worst_day   = float(daily_arr.min())
        daily_viols = int((daily_arr < -daily_limit).sum())
        total_pnl   = float(daily_arr.sum())

        # Trailing DD on this sizing
        equity = np.cumsum(daily_arr) + 150_000
        peak   = np.maximum.accumulate(equity)
        max_dd = float((peak - equity).max())

        results.append({
            "risk_per_trade_usd":  risk_per_trade,
            "worst_single_day_usd": round(worst_day, 2),
            "daily_limit_breaches": daily_viols,
            "max_trailing_dd_usd":  round(max_dd, 2),
            "total_pnl_usd":        round(total_pnl, 2),
        })
    return results


def drawdown_recovery(daily_port: pd.Series, account: float) -> dict:
    arr    = daily_port.sort_index().values
    equity = account + np.cumsum(arr)
    peak   = np.maximum.accumulate(equity)
    dd_arr = peak - equity

    max_dd_idx = int(np.argmax(dd_arr))
    max_dd     = float(dd_arr[max_dd_idx])

    # Find recovery: first day after max_dd_idx where equity >= peak at max_dd_idx
    recovery_days = None
    if max_dd > 0:
        peak_at_dd = float(peak[max_dd_idx])
        for j in range(max_dd_idx + 1, len(equity)):
            if equity[j] >= peak_at_dd:
                recovery_days = j - max_dd_idx
                break

    # Average recovery across all DD troughs > 20% of max
    threshold = max_dd * 0.2
    recoveries = []
    i = 0
    while i < len(dd_arr):
        if dd_arr[i] > threshold:
            trough = i
            while trough < len(dd_arr) and dd_arr[trough] > 0:
                trough += 1
            if trough < len(dd_arr):
                recoveries.append(trough - i)
            i = trough
        else:
            i += 1

    return {
        "max_dd_usd":              round(max_dd, 2),
        "max_dd_day_index":        int(max_dd_idx),
        "recovery_days_from_max":  recovery_days,
        "avg_recovery_days":       round(float(np.mean(recoveries)), 1) if recoveries else None,
        "n_drawdown_episodes":     len(recoveries),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 70)
    logger.info("  PRE-LIVE PORTFOLIO ANALYSIS — FINAL GATE")
    logger.info("  9 Survivors: comprehensive risk & robustness check")
    logger.info("=" * 70)

    # Step 1: Load all survivors
    survivor_data = []
    for key in SURVIVORS:
        logger.info(f"\nLoading {key}...")
        s = load_survivor(key)
        survivor_data.append(s)

    # Build combined daily portfolio P&L (unit sizing)
    all_trades = pd.concat(
        [s["trades"][["entry_time", "trade_date", "dollar_pnl", "stop_dist_usd"]].assign(
                           strategy=s["key"],
                           has_er=s["has_er"])
         for s in survivor_data],
        ignore_index=True
    ).sort_values("entry_time")

    daily_port = all_trades.groupby("trade_date")["dollar_pnl"].sum().sort_index()
    daily_arr  = daily_port.values

    # Save daily P&L cache
    daily_port.reset_index().rename(columns={"trade_date": "date",
                                              "dollar_pnl": "pnl_usd"}).to_csv(
        OUT_CSV, index=False)
    logger.info(f"\nDaily P&L cached to {OUT_CSV}  ({len(daily_port)} trading days)")

    # ── Section 1: Parameter Stability ───────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  1. PARAMETER STABILITY ACROSS WFO FOLDS")
    logger.info("=" * 70)
    logger.info(f"  {'Strategy':30s} {'Folds':>6} {'Stability':>10} {'Verdict':>10}")
    logger.info("  " + "-" * 60)

    param_stability_results = []
    for s in survivor_data:
        score = s["stability"]
        verdict = "EXCELLENT" if score >= 0.9 else ("GOOD" if score >= 0.7 else
                  "MODERATE" if score >= 0.5 else "WEAK")
        logger.info(f"  {s['key']:30s} {s['n_folds']:>6}     {score:6.3f}  {verdict}")
        param_stability_results.append({
            "key":     s["key"],
            "n_folds": s["n_folds"],
            "score":   round(score, 3),
            "verdict": verdict,
        })
        if s["fold_params"]:
            # Show which param changed across folds
            all_keys = list(s["fold_params"][0].keys()) if s["fold_params"] else []
            for pk in all_keys:
                vals = [str(fp.get(pk)) for fp in s["fold_params"]]
                ctr = Counter(vals)
                mode, mode_n = ctr.most_common(1)[0]
                if mode_n < len(vals):
                    logger.info(f"    {pk}: {vals} (mode={mode} {mode_n}/{len(vals)})")

    # ── Section 2: Trade Statistics ───────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  2. TRADE STATISTICS PER STRATEGY")
    logger.info("=" * 70)
    logger.info(f"  {'Strategy':28s} {'n':>5} {'WR%':>6} {'AvgW$':>8} {'AvgL$':>8} "
                f"{'Payoff':>7} {'MaxCL':>6} {'HoldH':>6}")
    logger.info("  " + "-" * 78)

    trade_stats_results = []
    for s in survivor_data:
        ts = trade_statistics(s["trades"], s["point_value"])
        trade_stats_results.append({"key": s["key"], **ts})
        logger.info(f"  {s['key']:28s} {ts['n']:>5} {ts['win_rate']:>5.1f}% "
                    f"{ts['avg_win_usd']:>+8.2f} {ts['avg_loss_usd']:>+8.2f} "
                    f"{ts['payoff_ratio'] or 0:>7.2f} {ts['max_consec_losses']:>6} "
                    f"{ts['avg_hold_hrs'] or 0:>6.1f}")
    logger.info(f"\n  Largest single win:  ${max(t['largest_win_usd'] for t in trade_stats_results):+,.2f}")
    logger.info(f"  Largest single loss: ${min(t['largest_loss_usd'] for t in trade_stats_results):+,.2f}")
    logger.info(f"  Max consec losses (any strategy): {max(t['max_consec_losses'] for t in trade_stats_results)}")

    # ── Section 3: Monthly P&L ────────────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  3. MONTHLY P&L BREAKDOWN (unit sizing, 9-strategy portfolio)")
    logger.info("=" * 70)

    mp = monthly_pnl(daily_port)
    logger.info(f"  Trading months:         {mp['n_months']}")
    logger.info(f"  Profitable months:      {mp['n_profitable_months']}/{mp['n_months']}  "
                f"({mp['pct_profitable']:.1f}%)")
    logger.info(f"  Avg monthly P&L:        ${mp['avg_monthly_pnl']:+,.2f}")
    logger.info(f"  Best month:             {mp['best_month']}  ${mp['best_month_pnl']:+,.2f}")
    logger.info(f"  Worst month:            {mp['worst_month']}  ${mp['worst_month_pnl']:+,.2f}")
    logger.info(f"  Max consecutive losing: {mp['max_consec_losing']} months")

    # Print full monthly table
    logger.info(f"\n  Monthly P&L table:")
    logger.info(f"  {'Month':>8} {'P&L':>10}    {'Month':>8} {'P&L':>10}    "
                f"{'Month':>8} {'P&L':>10}")
    recs = mp["monthly_records"]
    thirds = len(recs) // 3
    for i in range(min(thirds, len(recs))):
        r0 = recs[i]
        r1 = recs[i + thirds] if i + thirds < len(recs) else {"month": "", "pnl": 0}
        r2 = recs[i + 2*thirds] if i + 2*thirds < len(recs) else {"month": "", "pnl": 0}
        sign1 = "+" if r1["pnl"] >= 0 else ""
        sign2 = "+" if r2["pnl"] >= 0 else ""
        logger.info(f"  {r0['month']:>8} {r0['pnl']:>+10,.0f}    "
                    f"{r1['month']:>8} {sign1}{r1['pnl']:>9,.0f}    "
                    f"{r2['month']:>8} {sign2}{r2['pnl']:>9,.0f}")

    # ── Section 4: Monte Carlo ────────────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info(f"  4. MONTE CARLO SIMULATION  ({N_MONTE_CARLO:,} paths, bootstrap daily P&L)")
    logger.info(f"     Account=${PORT_ACCOUNT_SIZE:,.0f}  "
                f"Daily limit=${PORT_DAILY_LIMIT:,.0f}  "
                f"Trail DD=${PORT_TRAIL_DD:,.0f}")
    logger.info("=" * 70)

    mc = monte_carlo(daily_arr, N_MONTE_CARLO,
                     PORT_ACCOUNT_SIZE, PORT_DAILY_LIMIT, PORT_TRAIL_DD)

    logger.info(f"  P(trailing DD > ${PORT_TRAIL_DD:,.0f}):  {mc['p_trail_dd_breach']*100:.2f}%")
    logger.info(f"  P(any day > ${PORT_DAILY_LIMIT:,.0f} loss): {mc['p_daily_limit_breach']*100:.2f}%")
    logger.info(f"\n  Worst-day distribution (across {N_MONTE_CARLO:,} simulated runs):")
    logger.info(f"    Median worst day:   ${mc['worst_day_usd']['p50']:+,.2f}")
    logger.info(f"    25th pct worst day: ${mc['worst_day_usd']['p25']:+,.2f}")
    logger.info(f"    10th pct worst day: ${mc['worst_day_usd']['p10']:+,.2f}")
    logger.info(f"    5th  pct worst day: ${mc['worst_day_usd']['p05']:+,.2f}")
    logger.info(f"    1st  pct worst day: ${mc['worst_day_usd']['p01']:+,.2f}")
    logger.info(f"\n  Max trailing DD distribution (unit sizing):")
    logger.info(f"    Median max DD:  ${mc['max_trailing_dd_usd']['p50']:+,.2f}")
    logger.info(f"    75th pct max DD:${mc['max_trailing_dd_usd']['p75']:+,.2f}")
    logger.info(f"    90th pct max DD:${mc['max_trailing_dd_usd']['p90']:+,.2f}")
    logger.info(f"    95th pct max DD:${mc['max_trailing_dd_usd']['p95']:+,.2f}")
    logger.info(f"    99th pct max DD:${mc['max_trailing_dd_usd']['p99']:+,.2f}")
    logger.info(f"\n  12-year total P&L (simulated):")
    logger.info(f"    5th  pct: ${mc['p05_final_pnl']:+,.2f}")
    logger.info(f"    Median:   ${mc['median_final_pnl']:+,.2f}")
    logger.info(f"    95th pct: ${mc['p95_final_pnl']:+,.2f}")

    # ── Section 5: Sizing Sensitivity ─────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  5. SIZING SENSITIVITY — Equal-Risk per trade")
    logger.info(f"     Topstep daily limit: ${PORT_DAILY_LIMIT:,.0f}")
    logger.info("=" * 70)
    logger.info(f"  {'Risk/trade':>12} {'Worst day':>12} {'Daily breaches':>16} "
                f"{'Max Trail DD':>14} {'Total P&L':>12}")
    logger.info("  " + "-" * 72)

    sizing_results = sizing_sensitivity(survivor_data, PORT_DAILY_LIMIT, PORT_TRAIL_DD)
    for r in sizing_results:
        breach_flag = " *** RISK ***" if r["daily_limit_breaches"] > 0 else ""
        dd_flag = " *** RISK ***" if r["max_trailing_dd_usd"] > PORT_TRAIL_DD else ""
        logger.info(f"  ${r['risk_per_trade_usd']:>10,} "
                    f"${r['worst_single_day_usd']:>+11,.0f} "
                    f"         {r['daily_limit_breaches']:>4}{breach_flag}")
        logger.info(f"  {'':12s} {'':12s} Max trail DD: ${r['max_trailing_dd_usd']:>+10,.0f}{dd_flag}  "
                    f"Total P&L: ${r['total_pnl_usd']:>+10,.0f}")
        logger.info("  " + "·" * 60)

    # ── Section 6: Drawdown Recovery ─────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  6. DRAWDOWN RECOVERY ANALYSIS")
    logger.info("=" * 70)

    dd_rec = drawdown_recovery(daily_port, PORT_ACCOUNT_SIZE)
    logger.info(f"  Historical max DD (unit sizing): ${dd_rec['max_dd_usd']:+,.2f}")
    logger.info(f"  Recovery from max DD:            {dd_rec['recovery_days_from_max']} trading days"
                if dd_rec['recovery_days_from_max'] else
                "  Max DD still in recovery at end of sample")
    logger.info(f"  Drawdown episodes (>20% of max): {dd_rec['n_drawdown_episodes']}")
    logger.info(f"  Avg recovery time:               "
                f"{dd_rec['avg_recovery_days']} trading days"
                if dd_rec['avg_recovery_days'] else "  n/a")

    # ── Final Verdict ─────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("  PRE-LIVE GATE SUMMARY")
    logger.info("=" * 70)

    checks = {
        "Parameter stability (all GOOD+)":
            all(r["score"] >= 0.7 for r in param_stability_results),
        "Win rate all strategies > 40%":
            all(t["win_rate"] > 40 for t in trade_stats_results),
        "Monthly profitable > 60%":
            mp["pct_profitable"] > 60,
        "Max consec losing months <= 3":
            mp["max_consec_losing"] <= 3,
        f"P(trail DD breach ${PORT_TRAIL_DD/1000:.0f}k) < 5%":
            mc["p_trail_dd_breach"] < 0.05,
        f"P(daily limit breach) < 20%":
            mc["p_daily_limit_breach"] < 0.20,
        "5th pct 12yr P&L > $0":
            mc["p05_final_pnl"] > 0,
        "Worst day (1pct) > -$3,000 unit sizing":
            mc["worst_day_usd"]["p01"] > -3000,
    }

    all_pass = True
    for desc, passed in checks.items():
        icon = "PASS" if passed else "FAIL"
        logger.info(f"  [{icon}] {desc}")
        if not passed:
            all_pass = False

    gate_verdict = "ALL GATES CLEAR — PROCEED TO LIVE" if all_pass else "REVIEW REQUIRED"
    logger.info(f"\n  >> PRE-LIVE GATE: {gate_verdict}")
    logger.info("=" * 70)

    # ── Save results ──────────────────────────────────────────────────────────
    output = {
        "timestamp":           pd.Timestamp.now().isoformat(),
        "n_survivors":         len(survivor_data),
        "survivor_keys":       SURVIVORS,
        "param_stability":     param_stability_results,
        "trade_stats":         trade_stats_results,
        "monthly_pnl":         mp,
        "monte_carlo":         mc,
        "sizing_sensitivity":  sizing_results,
        "dd_recovery":         dd_rec,
        "gate_checks":         {k: bool(v) for k, v in checks.items()},
        "gate_verdict":        gate_verdict,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(output, f, indent=2, default=str)
    logger.info(f"\nResults written to: {OUT_JSON}")


if __name__ == "__main__":
    main()
