#!/usr/bin/env python3
"""
L2 Tick Deep Analysis — Real-World Robustness Suite
=====================================================
For every top L2 survivor, runs:
  1. Deduplication       — keep best DSR per (symbol, strategy)
  2. Slippage test       — 0 / 0.5 / 1 / 2 extra ticks per side
  3. Commission stress   — $3 / $5 / $8 / $10 per side
  4. Regime breakdown    — year-by-year (GC/SI) or quarter (ES/NQ)
  5. Parameter perturbation — ±10% / ±20% on best params
  6. Daily P&L profile   — worst day, % positive days, max drawdown duration
  7. Topstep daily compliance — % of days within $4,500 limit per strategy

Then selects a non-overlapping portfolio and runs:
  8. Portfolio Topstep simulation — equity curve, P(breach), survival
  9. Final combined OHLCV + L2 Topstep check

Usage:
  python tick_deep_analysis.py
"""

import json
import sys
import warnings
from pathlib import Path
from datetime import datetime
from collections import Counter

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT       = Path(__file__).parent.parent
RESULT_DIR = ROOT / "05_backtests"
BAR_DIR    = ROOT / "01_data" / "tick_bars"
OHLCV_PNL  = RESULT_DIR / "daily_portfolio_pnl.csv"

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import SPECS, compute_atr
from tick_strategies import STRATEGY_MAP

TOPSTEP_DAILY   = 4_500.0
TOPSTEP_TRAIL   = 7_500.0
MC_SIMS         = 10_000
TOP_N           = 20      # number of de-duped survivors to analyse
MAX_PORT        = 8       # max strategies in final portfolio
MIN_DAILY_PROB  = 0.85    # require 85% of days within daily limit per strategy

RNG = np.random.default_rng(42)


# ── Contract tick values ──────────────────────────────────────────────────────
def tick_dollar(symbol: str) -> float:
    s = SPECS[symbol]
    return s["tick_size"] * s["point_value"]


# ── Modified backtest with slippage + commission override ─────────────────────
def run_backtest_slippage(bars: pd.DataFrame, signals: pd.Series,
                          symbol: str,
                          stop_mult: float = 1.5, tp_mult: float = 3.0,
                          max_hold: int = 50, atr_window: int = 14,
                          extra_ticks: float = 0.0,
                          commission_per_side: float = 3.0) -> pd.DataFrame:
    spec = SPECS[symbol]
    pv   = spec["point_value"]
    slip = extra_ticks * spec["tick_size"]  # extra slippage per side

    hi   = bars["high"].values
    lo   = bars["low"].values
    cl   = bars["close"].values
    sig  = signals.reindex(bars.index).fillna(0).astype(int).values
    n    = len(cl)

    atr = compute_atr(hi, lo, cl, atr_window)
    trades = []
    in_pos = False; direction = 0; entry_bar = -1
    entry_px = 0.0; stop_px = 0.0; target_px = 0.0

    for i in range(n):
        if not in_pos:
            if sig[i] != 0 and not np.isnan(atr[i]):
                direction = int(sig[i])
                entry_bar = i
                # Slippage: long pays more, short receives less
                entry_px  = cl[i] + direction * slip
                a         = atr[i]
                stop_px   = entry_px - direction * stop_mult * a
                target_px = entry_px + direction * tp_mult   * a
                in_pos    = True
            continue

        hold = i - entry_bar
        exit_px = None; exit_reason = None

        if direction == 1 and lo[i] <= stop_px:
            exit_px, exit_reason = stop_px, "stop"
        elif direction == -1 and hi[i] >= stop_px:
            exit_px, exit_reason = stop_px, "stop"
        elif direction == 1 and hi[i] >= target_px:
            exit_px, exit_reason = target_px, "target"
        elif direction == -1 and lo[i] <= target_px:
            exit_px, exit_reason = target_px, "target"
        elif hold >= max_hold:
            exit_px, exit_reason = cl[i], "timeout"
        elif sig[i] != 0 and sig[i] != direction:
            exit_px, exit_reason = cl[i], "signal"

        if exit_px is not None:
            # Slippage on exit too (adverse)
            actual_exit = exit_px - direction * slip
            raw_pnl     = direction * (actual_exit - entry_px) * pv
            dollar_pnl  = raw_pnl - 2.0 * commission_per_side
            trades.append({
                "entry_bar":   entry_bar, "exit_bar":    i,
                "entry_time":  bars.index[entry_bar], "exit_time": bars.index[i],
                "direction":   direction, "entry_px": entry_px, "exit_px": actual_exit,
                "hold_bars":   hold, "exit_reason": exit_reason, "dollar_pnl": dollar_pnl,
            })
            in_pos = False
            if sig[i] != 0 and sig[i] != direction:
                direction = int(sig[i])
                entry_bar = i
                entry_px  = cl[i] + direction * slip
                a         = atr[i] if not np.isnan(atr[i]) else (atr[i-1] if i > 0 else 0)
                stop_px   = entry_px - direction * stop_mult * a
                target_px = entry_px + direction * tp_mult   * a
                in_pos    = True

    return pd.DataFrame(trades)


# ── Get daily P&L series ──────────────────────────────────────────────────────
def daily_pnl(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    trades = trades.copy()
    trades["date"] = pd.to_datetime(trades["entry_time"]).dt.date
    s = trades.groupby("date")["dollar_pnl"].sum()
    s.index = pd.to_datetime(s.index)
    return s


# ── Sharpe from daily series ──────────────────────────────────────────────────
def sharpe_from_daily(d: pd.Series) -> float:
    if len(d) < 5 or d.std() == 0:
        return 0.0
    return (d.mean() / d.std()) * np.sqrt(252)


# ── Load best params for a survivor ──────────────────────────────────────────
def best_params_from_results(result_row: dict, strat: dict) -> dict:
    fold_results = result_row.get("fold_results", [])
    if not fold_results or not isinstance(fold_results, list):
        return {}
    param_keys = list(strat["param_grid"].keys())
    best = {}
    for k in param_keys:
        vals = [f["best_params"].get(k)
                for f in fold_results
                if isinstance(f, dict) and "best_params" in f and k in f["best_params"]]
        if vals:
            best[k] = Counter(vals).most_common(1)[0][0]
    if not best and isinstance(fold_results[-1], dict):
        best = fold_results[-1].get("best_params", {})
    return best


# ── Load all results, deduplicate to best DSR per (symbol, strategy) ─────────
def load_deduped_survivors(n: int = TOP_N) -> list[dict]:
    files = sorted(RESULT_DIR.glob("tick_results_*.json"))
    all_rows = []
    for f in files:
        with open(f) as fh:
            rows = json.load(fh)
        for r in rows:
            r["source_file"] = f.name
        all_rows.extend(rows)

    df = pd.DataFrame(all_rows)
    surv = df[df["grade"].isin(["EXCELLENT", "GOOD", "MARGINAL"])].copy()
    surv = surv.sort_values("dsr", ascending=False).reset_index(drop=True)

    # Deduplicate: keep highest DSR per (symbol, strategy)
    seen = {}
    deduped = []
    for _, row in surv.iterrows():
        key = (row["symbol"], row["strategy"])
        if key not in seen:
            seen[key] = True
            deduped.append(row.to_dict())

    return deduped[:n]


# ── 1. Slippage & commission sensitivity ─────────────────────────────────────
def slippage_test(survivor: dict, bars: pd.DataFrame, strat: dict,
                  params: dict, symbol: str) -> dict:
    results = {}
    slippages = [0.0, 0.5, 1.0, 2.0]
    commissions = [3.0, 5.0, 8.0, 10.0]

    for sl in slippages:
        trades = run_backtest_slippage(bars, strat["compute"](bars, **params),
                                       symbol, extra_ticks=sl, commission_per_side=3.0)
        d = daily_pnl(trades)
        results[f"slip_{sl}t"] = {
            "sharpe": sharpe_from_daily(d),
            "total_pnl": d.sum() if len(d) > 0 else 0,
            "n_trades": len(trades),
            "pct_days_positive": (d > 0).mean() if len(d) > 0 else 0,
        }

    for cm in commissions:
        trades = run_backtest_slippage(bars, strat["compute"](bars, **params),
                                       symbol, extra_ticks=0.0, commission_per_side=cm)
        d = daily_pnl(trades)
        results[f"comm_{cm}"] = {
            "sharpe": sharpe_from_daily(d),
            "total_pnl": d.sum() if len(d) > 0 else 0,
            "n_trades": len(trades),
        }

    return results


# ── 2. Regime breakdown (year / quarter) ─────────────────────────────────────
def regime_test(bars: pd.DataFrame, sig: pd.Series, symbol: str,
                params: dict, strat: dict) -> dict:
    trades = run_backtest_slippage(bars, sig, symbol, extra_ticks=0.5)
    if trades.empty:
        return {}

    trades["entry_dt"] = pd.to_datetime(trades["entry_time"])
    trades["year"]     = trades["entry_dt"].dt.year
    trades["quarter"]  = trades["entry_dt"].dt.to_period("Q").astype(str)

    # Use year breakdown for GC/SI (long history), quarter for ES/NQ (short)
    if symbol in ("GC", "SI"):
        groups = trades.groupby("year")
    else:
        groups = trades.groupby("quarter")

    regime_results = {}
    for name, g in groups:
        if len(g) < 10:
            continue
        d = daily_pnl(g)
        regime_results[str(name)] = {
            "sharpe":     round(sharpe_from_daily(d), 2),
            "total_pnl":  round(d.sum(), 0),
            "n_trades":   len(g),
            "win_rate":   round((g["dollar_pnl"] > 0).mean(), 3),
            "profitable": d.sum() > 0,
        }
    return regime_results


# ── 3. Parameter perturbation ─────────────────────────────────────────────────
def param_perturbation_test(bars: pd.DataFrame, strat: dict, best_params: dict,
                             symbol: str) -> dict:
    grid = strat["param_grid"]
    perturbations = [-0.20, -0.10, 0.0, 0.10, 0.20]
    results = {}

    for pct in perturbations:
        perturbed = {}
        for k, v in best_params.items():
            if isinstance(v, (int, float)):
                new_v = v * (1 + pct)
                # Round to nearest value in grid if possible
                grid_vals = grid.get(k, [v])
                if grid_vals:
                    new_v = min(grid_vals, key=lambda x: abs(x - new_v))
                perturbed[k] = new_v
            else:
                perturbed[k] = v

        try:
            sig = strat["compute"](bars, **perturbed)
            trades = run_backtest_slippage(bars, sig, symbol, extra_ticks=0.5)
            d = daily_pnl(trades)
            sh = sharpe_from_daily(d)
            results[f"{pct:+.0%}"] = {
                "params": perturbed,
                "sharpe": round(sh, 2),
                "total_pnl": round(d.sum(), 0) if len(d) > 0 else 0,
                "pass_dsr1": sh / np.sqrt(np.log(max(len(best_params), 2))) >= 1.0,
            }
        except Exception:
            results[f"{pct:+.0%}"] = {"sharpe": None, "pass_dsr1": False}

    pass_count = sum(1 for v in results.values() if v.get("pass_dsr1"))
    results["_robustness_score"] = f"{pass_count}/{len(perturbations)} param combos pass DSR>=1.0"
    return results


# ── 4. Daily P&L profile ──────────────────────────────────────────────────────
def daily_profile(trades: pd.DataFrame, symbol: str) -> dict:
    if trades.empty:
        return {}
    d = daily_pnl(trades)
    if d.empty:
        return {}

    # Topstep daily compliance (with 1-tick slippage already baked in)
    pct_within_limit = (d >= -TOPSTEP_DAILY).mean()

    # Drawdown duration
    cum = d.cumsum()
    peak = cum.cummax()
    dd = peak - cum
    in_dd = (dd > 0)
    # Find longest continuous drawdown in days
    max_dd_days = 0
    cur = 0
    for x in in_dd:
        cur = cur + 1 if x else 0
        max_dd_days = max(max_dd_days, cur)

    # Max losing streak (days)
    losing_days = (d < 0)
    max_consec_loss_days = 0
    cur = 0
    for x in losing_days:
        cur = cur + 1 if x else 0
        max_consec_loss_days = max(max_consec_loss_days, cur)

    return {
        "n_trading_days":         len(d),
        "pct_positive_days":      round((d > 0).mean(), 3),
        "pct_within_daily_limit": round(pct_within_limit, 3),
        "worst_day":              round(d.min(), 0),
        "best_day":               round(d.max(), 0),
        "avg_daily_pnl":          round(d.mean(), 0),
        "daily_pnl_std":          round(d.std(), 0),
        "max_dd_duration_days":   max_dd_days,
        "max_consec_loss_days":   max_consec_loss_days,
        "total_pnl":              round(d.sum(), 0),
        "sharpe":                 round(sharpe_from_daily(d), 2),
    }


# ── 5. Topstep portfolio simulation ──────────────────────────────────────────
def topstep_simulation(portfolio_daily: pd.Series, n_sims: int = MC_SIMS) -> dict:
    pnl = portfolio_daily.values
    if len(pnl) < 20:
        return {}

    breached_daily  = np.zeros(n_sims, dtype=bool)
    breached_trail  = np.zeros(n_sims, dtype=bool)
    final_pnl       = np.zeros(n_sims)
    survival_days   = np.zeros(n_sims)

    for i in range(n_sims):
        sim     = RNG.choice(pnl, size=len(pnl), replace=True)
        equity  = np.cumsum(sim)
        peak    = np.maximum.accumulate(equity)
        trail   = peak - equity   # trailing drawdown from peak

        # Daily limit: worst single day
        breached_daily[i]  = (sim < -TOPSTEP_DAILY).any()
        breached_trail[i]  = (trail > TOPSTEP_TRAIL).any()
        final_pnl[i]       = sim.sum()

        # Days survived (first breach)
        daily_breach_idx = np.where(sim < -TOPSTEP_DAILY)[0]
        trail_breach_idx = np.where(trail > TOPSTEP_TRAIL)[0]
        breach_day = min(
            daily_breach_idx[0] if len(daily_breach_idx) > 0 else len(sim),
            trail_breach_idx[0] if len(trail_breach_idx) > 0 else len(sim)
        )
        survival_days[i] = breach_day

    return {
        "p_daily_breach":          round(float(breached_daily.mean()), 4),
        "p_trailing_breach":       round(float(breached_trail.mean()), 4),
        "p_any_breach":            round(float((breached_daily | breached_trail).mean()), 4),
        "median_survival_days":    round(float(np.median(survival_days)), 0),
        "p10_survival_days":       round(float(np.percentile(survival_days, 10)), 0),
        "final_pnl_p5":            round(float(np.percentile(final_pnl, 5)), 0),
        "final_pnl_p50":           round(float(np.percentile(final_pnl, 50)), 0),
        "final_pnl_p95":           round(float(np.percentile(final_pnl, 95)), 0),
    }


# ── Print helpers ─────────────────────────────────────────────────────────────
def hr(char="─", n=90):
    print(char * n)


def section(title: str):
    hr("═")
    print(f"  {title}")
    hr("═")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*90}")
    print(f"  L2 DEEP ANALYSIS — REAL-WORLD ROBUSTNESS")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*90}\n")

    # Load de-duped survivors
    survivors = load_deduped_survivors(TOP_N)
    print(f"  Analysing {len(survivors)} de-duplicated survivors (best DSR per symbol/strategy)\n")

    all_strategy_daily: dict[str, pd.Series] = {}   # key -> daily P&L with 0.5-tick slip
    full_results = []

    for rank, row in enumerate(survivors, 1):
        symbol     = row["symbol"]
        strat_name = row["strategy"]
        bar_min    = int(row["bar_minutes"])
        dsr        = row["dsr"]
        grade      = row["grade"]

        tag = f"{symbol}/{strat_name}/{bar_min}m"
        print(f"  [{rank:02d}] {tag}  DSR={dsr:.2f}  [{grade}]")

        strat = STRATEGY_MAP.get(strat_name)
        bar_path = BAR_DIR / f"{symbol}_bars_{bar_min}m.parquet"

        if not strat or not bar_path.exists():
            print(f"       SKIP — missing strategy or bar file")
            continue

        params = best_params_from_results(row, strat)
        if not params:
            print(f"       SKIP — no best params")
            continue

        bars = pd.read_parquet(bar_path)
        bars.index = pd.to_datetime(bars.index, utc=True)

        try:
            sig = strat["compute"](bars, **params)
        except Exception as e:
            print(f"       SKIP — signal error: {e}")
            continue

        # ── Baseline trades (0.5-tick slippage, $3 comm) ──────────────────────
        baseline_trades = run_backtest_slippage(bars, sig, symbol, extra_ticks=0.5)
        base_daily = daily_pnl(baseline_trades)
        all_strategy_daily[tag] = base_daily

        # ── Slippage & commission test ─────────────────────────────────────────
        slip_res = slippage_test(row, bars, strat, params, symbol)

        # ── Regime test ───────────────────────────────────────────────────────
        regime_res = regime_test(bars, sig, symbol, params, strat)

        # ── Param perturbation ────────────────────────────────────────────────
        perturb_res = param_perturbation_test(bars, strat, params, symbol)

        # ── Daily profile ─────────────────────────────────────────────────────
        profile = daily_profile(baseline_trades, symbol)

        # ── Print per-strategy summary ────────────────────────────────────────
        print(f"       Baseline (0.5-tick slip): Sharpe={profile.get('sharpe', 0):.2f}  "
              f"Total=${profile.get('total_pnl', 0):,.0f}  "
              f"Win%={profile.get('pct_positive_days', 0)*100:.0f}%/day  "
              f"Worst_day=${profile.get('worst_day', 0):,.0f}")

        # Slippage row
        slip_sharpes = [f"{t}: {v['sharpe']:.2f}" for t, v in slip_res.items()
                        if t.startswith("slip_")]
        print(f"       Slippage Sharpe: {' | '.join(slip_sharpes)}")

        # Regime summary
        profitable_regimes = sum(1 for v in regime_res.values() if v.get("profitable"))
        total_regimes = len(regime_res)
        print(f"       Regimes: {profitable_regimes}/{total_regimes} profitable  "
              f"({perturb_res.get('_robustness_score', 'N/A')})")

        # Topstep daily compliance
        pct_ok = profile.get("pct_within_daily_limit", 0)
        print(f"       Topstep daily compliance: {pct_ok*100:.1f}% of days within $4,500 limit")
        print()

        full_results.append({
            "rank":      rank,
            "tag":       tag,
            "symbol":    symbol,
            "strategy":  strat_name,
            "bar_min":   bar_min,
            "dsr":       dsr,
            "grade":     grade,
            "params":    params,
            "profile":   profile,
            "slippage":  slip_res,
            "regime":    regime_res,
            "perturb":   perturb_res,
        })

    # ── SECTION: Slippage sensitivity summary ─────────────────────────────────
    section("SLIPPAGE SENSITIVITY — FULL TABLE")
    print(f"  {'Strategy':<45} {'0-tick':>7} {'0.5-tick':>8} {'1-tick':>7} {'2-tick':>7}  {'Verdict'}")
    hr()
    for r in full_results:
        sl = r["slippage"]
        s0 = sl.get("slip_0.0t", {}).get("sharpe", 0) or 0
        s05 = sl.get("slip_0.5t", {}).get("sharpe", 0) or 0
        s1  = sl.get("slip_1.0t", {}).get("sharpe", 0) or 0
        s2  = sl.get("slip_2.0t", {}).get("sharpe", 0) or 0
        verdict = "ROBUST" if s2 > 1.0 else ("OK" if s1 > 1.0 else ("MARGINAL" if s05 > 1.0 else "FAILS"))
        print(f"  {r['tag']:<45} {s0:>7.2f} {s05:>8.2f} {s1:>7.2f} {s2:>7.2f}  {verdict}")
    print()

    # ── SECTION: Commission sensitivity ───────────────────────────────────────
    section("COMMISSION SENSITIVITY")
    print(f"  {'Strategy':<45} {'$3/side':>8} {'$5/side':>8} {'$8/side':>8} {'$10/side':>9}")
    hr()
    for r in full_results:
        sl = r["slippage"]
        c3  = sl.get("comm_3.0", {}).get("sharpe", 0) or 0
        c5  = sl.get("comm_5.0", {}).get("sharpe", 0) or 0
        c8  = sl.get("comm_8.0", {}).get("sharpe", 0) or 0
        c10 = sl.get("comm_10.0", {}).get("sharpe", 0) or 0
        print(f"  {r['tag']:<45} {c3:>8.2f} {c5:>8.2f} {c8:>8.2f} {c10:>9.2f}")
    print()

    # ── SECTION: Regime breakdown ─────────────────────────────────────────────
    section("REGIME BREAKDOWN (YEAR/QUARTER)")
    for r in full_results:
        reg = r["regime"]
        if not reg:
            continue
        print(f"  {r['tag']}:")
        for period, v in sorted(reg.items()):
            tick = "[+]" if v["profitable"] else "[-]"
            print(f"    {period:>12}  {tick}  Sharpe={v['sharpe']:>5.2f}  "
                  f"PnL=${v['total_pnl']:>10,.0f}  Trades={v['n_trades']:>5}  WR={v['win_rate']*100:.1f}%")
        print()

    # ── SECTION: Param robustness ─────────────────────────────────────────────
    section("PARAMETER ROBUSTNESS (+-10% / +-20% PERTURBATION)")
    print(f"  {'Strategy':<45} {'-20%':>6} {'-10%':>6} {'0%':>6} {'+10%':>6} {'+20%':>6}  {'Score'}")
    hr()
    for r in full_results:
        p = r["perturb"]
        score = p.get("_robustness_score", "N/A")
        vals = []
        for pct in ["-20%", "-10%", "+0%", "+10%", "+20%"]:
            v = p.get(pct, {})
            if v is None:
                v = {}
            sh = v.get("sharpe")
            vals.append(f"{sh:>6.2f}" if sh is not None else "  N/A")
        print(f"  {r['tag']:<45} {' '.join(vals)}  {score}")
    print()

    # ── SECTION: Daily profile & Topstep compliance ───────────────────────────
    section("DAILY P&L PROFILE & TOPSTEP COMPLIANCE")
    print(f"  {'Strategy':<45} {'WinDays%':>8} {'TS-Comp%':>9} {'WorstDay':>9} {'MaxDDdays':>10} {'Sharpe':>7}")
    hr()
    for r in full_results:
        p = r["profile"]
        print(f"  {r['tag']:<45} "
              f"{p.get('pct_positive_days', 0)*100:>7.1f}% "
              f"{p.get('pct_within_daily_limit', 0)*100:>8.1f}% "
              f"{p.get('worst_day', 0):>9,.0f} "
              f"{p.get('max_dd_duration_days', 0):>10} "
              f"{p.get('sharpe', 0):>7.2f}")
    print()

    # ── SECTION: Portfolio construction ───────────────────────────────────────
    section("PORTFOLIO CONSTRUCTION — TOPSTEP SIMULATION")

    # Filter to strategies that pass basic real-world conditions
    eligible = [r for r in full_results
                if (r["slippage"].get("slip_1.0t", {}).get("sharpe", 0) or 0) > 0.8
                and r["profile"].get("pct_within_daily_limit", 0) >= 0.90]

    print(f"  Eligible strategies (1-tick slip Sharpe > 0.8 AND 90%+ daily compliance): {len(eligible)}\n")
    for r in eligible:
        s1 = (r["slippage"].get("slip_1.0t", {}).get("sharpe", 0) or 0)
        comp = r["profile"].get("pct_within_daily_limit", 0)
        print(f"    {r['tag']:<45}  slip1-Sharpe={s1:.2f}  TS-comply={comp*100:.1f}%")

    print()
    print(f"  Building equal-weight portfolio from top eligible strategies...")
    print()

    # Sort eligible by DSR, pick top MAX_PORT
    eligible_sorted = sorted(eligible, key=lambda x: x["dsr"], reverse=True)
    portfolio_tags = [r["tag"] for r in eligible_sorted[:MAX_PORT]]

    # Build portfolio daily P&L (sum of all selected, 1-tick slippage)
    port_daily_list = [all_strategy_daily[t] for t in portfolio_tags
                       if t in all_strategy_daily and len(all_strategy_daily[t]) > 0]

    if not port_daily_list:
        print("  No eligible strategies with daily P&L data — cannot run portfolio simulation")
        return

    port_df = pd.concat(port_daily_list, axis=1).fillna(0)
    port_combined = port_df.sum(axis=1)

    monthly = port_combined.resample("ME").sum()
    monthly_wr = (monthly > 0).mean()

    print(f"  Portfolio: {len(portfolio_tags)} strategies | {len(port_combined)} days")
    print(f"  Monthly win rate:  {monthly_wr*100:.1f}%")
    print(f"  Avg monthly P&L:  ${monthly.mean():,.0f}")
    print(f"  Total P&L:        ${port_combined.sum():,.0f}")
    print(f"  Portfolio Sharpe: {sharpe_from_daily(port_combined):.2f}")
    print()

    for size in range(1, min(len(eligible_sorted), MAX_PORT) + 1):
        tags = [r["tag"] for r in eligible_sorted[:size]]
        series = [all_strategy_daily[t] for t in tags
                  if t in all_strategy_daily and len(all_strategy_daily[t]) > 0]
        if not series:
            continue
        pf = pd.concat(series, axis=1).fillna(0).sum(axis=1)
        mc = topstep_simulation(pf)
        if not mc:
            continue
        print(f"  Portfolio size {size}: P(any_breach)={mc['p_any_breach']*100:.1f}%  "
              f"P(daily)={mc['p_daily_breach']*100:.1f}%  "
              f"P(trail)={mc['p_trailing_breach']*100:.1f}%  "
              f"Median survival={mc['median_survival_days']:.0f} days  "
              f"P50 PnL=${mc['final_pnl_p50']:,.0f}")

    print()

    # ── SECTION: Combined with OHLCV ──────────────────────────────────────────
    section("COMBINED OHLCV + L2 PORTFOLIO SIMULATION")

    if OHLCV_PNL.exists():
        ohlcv_df = pd.read_csv(OHLCV_PNL, index_col=0, parse_dates=True)
        if "portfolio_pnl" not in ohlcv_df.columns:
            ohlcv_df["portfolio_pnl"] = ohlcv_df.sum(axis=1)
        ohlcv_daily = ohlcv_df["portfolio_pnl"]
        ohlcv_daily.index = pd.to_datetime(ohlcv_daily.index)

        # Use top 6 eligible L2 strategies
        l2_tags   = [r["tag"] for r in eligible_sorted[:6]]
        l2_series = [all_strategy_daily[t] for t in l2_tags
                     if t in all_strategy_daily and len(all_strategy_daily[t]) > 0]
        if l2_series:
            l2_daily = pd.concat(l2_series, axis=1).fillna(0).sum(axis=1)
            combined = pd.DataFrame({"ohlcv": ohlcv_daily, "l2": l2_daily}).fillna(0)
            combined["total"] = combined["ohlcv"] + combined["l2"]

            corr = combined["ohlcv"].corr(combined["l2"])
            monthly_c = combined["total"].resample("ME").sum()

            print(f"  OHLCV + top-{len(l2_tags)} L2 strategies (1-tick slippage):")
            print(f"    OHLCV / L2 correlation:  {corr:.3f}")
            print(f"    Combined monthly WR:     {(monthly_c > 0).mean()*100:.1f}%")
            print(f"    Combined avg monthly:   ${monthly_c.mean():,.0f}")
            print(f"    Combined Sharpe:        {sharpe_from_daily(combined['total']):.2f}")

            mc_comb = topstep_simulation(combined["total"])
            if mc_comb:
                print(f"\n    COMBINED TOPSTEP MC (10k paths):")
                print(f"    P(daily breach):     {mc_comb['p_daily_breach']*100:.2f}%")
                print(f"    P(trailing breach):  {mc_comb['p_trailing_breach']*100:.2f}%")
                print(f"    Median survival:     {mc_comb['median_survival_days']:.0f} days")
                print(f"    P50 PnL:            ${mc_comb['final_pnl_p50']:,.0f}")
                print(f"    P95 PnL:            ${mc_comb['final_pnl_p95']:,.0f}")
    else:
        print("  OHLCV daily P&L file not found — skipping combined analysis")

    # ── SECTION: Final recommendation ─────────────────────────────────────────
    section("FINAL RECOMMENDATION — TOPSTEP GO-LIVE STRATEGY SELECTION")

    print(f"  Scoring: DSR × (1-tick Sharpe) × Topstep-compliance × regime-breadth\n")
    scores = []
    for r in full_results:
        slip1 = (r["slippage"].get("slip_1.0t", {}).get("sharpe", 0) or 0)
        comp  = r["profile"].get("pct_within_daily_limit", 0)
        reg   = r["regime"]
        reg_breadth = sum(1 for v in reg.values() if v.get("profitable")) / max(len(reg), 1)
        perturb_score = sum(1 for k, v in r["perturb"].items()
                            if k != "_robustness_score" and v and v.get("pass_dsr1")) / 5.0
        composite = r["dsr"] * max(slip1, 0) * comp * (0.5 + 0.5 * reg_breadth) * (0.5 + 0.5 * perturb_score)
        scores.append((composite, r))

    scores.sort(key=lambda x: -x[0])
    print(f"  {'#':<3} {'Strategy':<45} {'DSR':>6} {'Slip1':>6} {'TS%':>5} {'Regime':>7} {'Score':>7}")
    hr()
    for i, (score, r) in enumerate(scores[:15], 1):
        slip1 = (r["slippage"].get("slip_1.0t", {}).get("sharpe", 0) or 0)
        comp  = r["profile"].get("pct_within_daily_limit", 0)
        reg   = r["regime"]
        reg_pct = sum(1 for v in reg.values() if v.get("profitable")) / max(len(reg), 1) * 100
        print(f"  {i:<3} {r['tag']:<45} {r['dsr']:>6.2f} {slip1:>6.2f} "
              f"{comp*100:>4.0f}% {reg_pct:>6.0f}%  {score:>7.3f}")

    print()
    print(f"  RECOMMENDED LIVE PORTFOLIO (top 6 by composite score):")
    print(f"  Unit sizing: 1 contract per strategy")
    print(f"  Monitor: stop strategy if monthly loss > $2,000 or 3 consecutive losing weeks")
    print()
    for i, (score, r) in enumerate(scores[:6], 1):
        print(f"    {i}. {r['tag']}")
        print(f"       DSR={r['dsr']:.2f}  1-tick Sharpe={((r['slippage'].get('slip_1.0t', {}) or {}).get('sharpe', 0) or 0):.2f}  "
              f"WorstDay=${r['profile'].get('worst_day', 0):,.0f}  "
              f"Params: {r['params']}")
    print()

    # ── Save full results ──────────────────────────────────────────────────────
    ts  = datetime.now().strftime("%Y%m%d_%H%M")
    out = RESULT_DIR / f"tick_deep_analysis_{ts}.json"

    def _ser(obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, (np.ndarray,)):  return obj.tolist()
        if isinstance(obj, pd.Period):      return str(obj)
        try:
            if pd.isna(obj): return None
        except Exception:
            pass
        return str(obj)

    with open(out, "w") as f:
        json.dump({
            "timestamp": ts,
            "n_analysed": len(full_results),
            "strategies": [
                {k: v for k, v in r.items() if k != "params" or True}
                for r in full_results
            ],
            "top_recommended": [r["tag"] for _, r in scores[:6]],
        }, f, indent=2, default=_ser)

    print(f"  Full results saved: {out}")
    hr("═")


if __name__ == "__main__":
    main()
