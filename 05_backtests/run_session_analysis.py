#!/usr/bin/env python3
"""
run_session_analysis.py
=======================
Part A: Session filter analysis for V6/V7/V8 strategies (IDs 16-38).
         - Groups trades by UTC entry hour
         - Finds hours where sum_pnl < 0 AND n_trades >= 3
         - Computes baseline Sharpe vs. filtered Sharpe

Part B: Combined portfolio backtest for ALL strategies (V1-V4, IDs 1-15 + V678 IDs 16-38).
         - Merges all trade logs by date
         - Computes daily portfolio P&L, Sharpe, max drawdown, % positive months, total P&L

Outputs:
  ..\05_backtests\session_filter_analysis.json
  ..\05_backtests\combined_portfolio_backtest.json
"""

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Path setup ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
ROOT       = SCRIPT_DIR.parent
CODEBASE   = ROOT / "04_codebase"
BAR_DIR    = ROOT / "01_data" / "tick_bars"

sys.path.insert(0, str(CODEBASE))

from tick_backtest_engine import run_backtest

# ── Strategy module imports ────────────────────────────────────────────────────
try:
    from tick_strategies import STRATEGY_MAP
    print("  [OK] tick_strategies (V1) loaded")
except Exception as e:
    print(f"  [WARN] tick_strategies failed: {e}")
    STRATEGY_MAP = {}

try:
    from tick_strategies_v2 import STRAT_MAP as STRAT_MAP_V2
    print("  [OK] tick_strategies_v2 loaded")
except Exception as e:
    print(f"  [WARN] tick_strategies_v2 failed: {e}")
    STRAT_MAP_V2 = {}

try:
    from tick_strategies_v3 import STRAT_MAP_V3
    print("  [OK] tick_strategies_v3 loaded")
except Exception as e:
    print(f"  [WARN] tick_strategies_v3 failed: {e}")
    STRAT_MAP_V3 = {}

try:
    from tick_strategies_v4 import STRAT_MAP_V4
    print("  [OK] tick_strategies_v4 loaded")
except Exception as e:
    print(f"  [WARN] tick_strategies_v4 failed: {e}")
    STRAT_MAP_V4 = {}

try:
    from tick_strategies_v5 import STRAT_MAP_V5
    print("  [OK] tick_strategies_v5 loaded")
except Exception as e:
    print(f"  [WARN] tick_strategies_v5 failed: {e}")
    STRAT_MAP_V5 = {}

try:
    from tick_strategies_v6 import STRAT_MAP_V6
    print("  [OK] tick_strategies_v6 loaded")
except Exception as e:
    print(f"  [WARN] tick_strategies_v6 failed: {e}")
    STRAT_MAP_V6 = {}

try:
    from tick_strategies_v7 import STRAT_MAP_V7
    print("  [OK] tick_strategies_v7 loaded")
except Exception as e:
    print(f"  [WARN] tick_strategies_v7 failed: {e}")
    STRAT_MAP_V7 = {}

try:
    from tick_strategies_v8 import STRAT_MAP_V8
    print("  [OK] tick_strategies_v8 loaded")
except Exception as e:
    print(f"  [WARN] tick_strategies_v8 failed: {e}")
    STRAT_MAP_V8 = {}


# ── Resolve strategy function from name + version ─────────────────────────────
def resolve_strategy(name: str, version: str):
    """Return the strategy compute function, or None if not found."""
    maps = {
        "v1": STRATEGY_MAP,
        "v2": STRAT_MAP_V2,
        "v3": STRAT_MAP_V3,
        "v4": STRAT_MAP_V4,
        "v5": STRAT_MAP_V5,
        "v6": STRAT_MAP_V6,
        "v7": STRAT_MAP_V7,
        "v8": STRAT_MAP_V8,
    }
    m = maps.get(version, {})
    entry = m.get(name)
    if entry is None:
        return None
    return entry["compute"]


# ── Load bars ─────────────────────────────────────────────────────────────────
_bar_cache = {}

def load_bars(symbol: str, tf_m: int) -> pd.DataFrame:
    key = (symbol, tf_m)
    if key in _bar_cache:
        return _bar_cache[key]
    path = BAR_DIR / f"{symbol}_bars_{tf_m}m.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Bar file not found: {path}")
    df = pd.read_parquet(path)
    # Ensure datetime index in UTC
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    _bar_cache[key] = df
    return df


# ── Sharpe from trade P&L series ──────────────────────────────────────────────
def compute_sharpe(trades: pd.DataFrame) -> float:
    """Annualised daily Sharpe from a trades DataFrame."""
    if trades.empty or len(trades) < 5:
        return 0.0
    daily = trades.set_index("entry_time")["dollar_pnl"].resample("D").sum()
    if daily.std() == 0:
        return 0.0
    return float(daily.mean() / daily.std() * np.sqrt(252))


# ══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO definition — all 38 strategies
# (id, symbol, tf_m, strat_name, params, version)
# ══════════════════════════════════════════════════════════════════════════════
PORTFOLIO = [
    (1,  "GC",  1,  "obi_threshold",               {"threshold": 0.3, "smooth_window": 1},                                                         "v1"),
    (2,  "ES", 15,  "cvd_divergence_large_print",  {"price_window": 20, "cvd_window": 10, "min_large": 1},                                          "v1"),
    (3,  "ES", 15,  "cvd_divergence",               {"price_window": 40, "cvd_window": 20, "threshold": 0.3},                                        "v1"),
    (4,  "ES", 15,  "tape_absorption",              {"price_window": 5, "vol_z_threshold": 1.5, "price_threshold": 0.001},                           "v1"),
    (5,  "NQ", 30,  "cvd_divergence_large_print",  {"price_window": 20, "cvd_window": 10, "min_large": 2},                                          "v1"),
    (6,  "NQ",  3,  "stop_hunt_reversal",           {"spike_bars": 1, "spike_pct": 0.001, "cvd_flip_window": 5},                                     "v1"),
    (7,  "ES",  3,  "prev_session_sweep",           {"level_window": 20, "cvd_flip_window": 3, "sweep_buffer": 0.0001},                             "v2"),
    (8,  "NQ", 30,  "range_contraction_break",      {"squeeze_pct": 30, "breakout_z": 1.0, "cvd_z": 0.5},                                           "v3"),
    (9,  "GC",  3,  "session_momentum_follow",      {"bias_z": 1.0, "follow_bars": 8, "break_pct": 0.0002},                                         "v3"),
    (10, "GC", 30,  "trade_absorption_signal",      {"ntrades_z": 1.2, "range_z": -0.3, "cvd_z": 0.4},                                             "v4"),
    (11, "ES", 30,  "avg_order_size_divergence",    {"window": 20, "z_thresh": 1.0, "price_thresh": 0.001},                                         "v4"),
    (12, "NQ", 30,  "trade_absorption_signal",      {"ntrades_z": 1.2, "range_z": -0.3, "cvd_z": 0.4},                                             "v4"),
    (13, "ES", 15,  "key_level_cvd_rejection",      {"key_level_window": 30, "cvd_window": 10, "rejection_atr_pct": 0.25},                         "v5"),
    (14, "NQ", 15,  "key_level_cvd_rejection",      {"key_level_window": 30, "cvd_window": 20, "rejection_atr_pct": 0.5},                          "v5"),
    (15, "GC",  5,  "key_level_cvd_rejection",      {"key_level_window": 10, "cvd_window": 10, "rejection_atr_pct": 1.0},                          "v5"),
    # V6/V7/V8
    (16, "GC", 30,  "vwap_mean_reversion",          {"z_thresh": 2.5, "vwap_window": 10},                                                          "v6"),
    (17, "GC", 30,  "pivot_reversal",               {"pivot_bars": 10, "bounce_atr_mult": 0.2, "atr_win": 10},                                     "v8"),
    (18, "SI", 30,  "opening_range_fakeout",        {"orb_bars": 12, "reentry_atr_pct": 0.2, "atr_window": 14},                                    "v6"),
    (19, "SI",  3,  "consecutive_close_momentum",   {"n": 5},                                                                                       "v8"),
    (20, "GC", 15,  "pivot_reversal",               {"pivot_bars": 20, "bounce_atr_mult": 0.2, "atr_win": 14},                                     "v8"),
    (21, "SI",  1,  "ema_crossover",                {"fast": 5, "slow": 34, "slope_bars": 5},                                                      "v7"),
    (22, "SI", 15,  "vwap_mean_reversion",          {"z_thresh": 2.5, "vwap_window": 10},                                                          "v6"),
    (23, "SI",  3,  "opening_range_fakeout",        {"orb_bars": 3, "reentry_atr_pct": 0.05, "atr_window": 14},                                    "v6"),
    (24, "GC", 15,  "donchian_breakout",            {"n": 40, "confirm": 1},                                                                        "v7"),
    (25, "SI",  5,  "consecutive_close_momentum",   {"n": 5},                                                                                       "v8"),
    (26, "SI", 30,  "ema_crossover",                {"fast": 13, "slow": 34, "slope_bars": 3},                                                     "v7"),
    (27, "GC", 15,  "consecutive_close_momentum",   {"n": 5},                                                                                       "v8"),
    (28, "SI", 30,  "ma_slope_regime",              {"ma_win": 20, "slope_bars": 3, "entry_rsi_win": 14, "rsi_ob": 60, "rsi_os": 40},              "v8"),
    (29, "SI",  5,  "ema_crossover",                {"fast": 5, "slow": 34, "slope_bars": 5},                                                      "v7"),
    (30, "SI", 15,  "consecutive_close_momentum",   {"n": 5},                                                                                       "v8"),
    (31, "SI",  1,  "consecutive_close_momentum",   {"n": 5},                                                                                       "v8"),
    (32, "GC", 15,  "close_position_momentum",      {"cp_window": 5, "cp_thresh": 0.75},                                                           "v8"),
    (33, "ES", 30,  "overnight_gap_fill",           {"gap_atr_mult": 0.3, "atr_window": 14},                                                       "v6"),
    (34, "ES", 15,  "overnight_gap_fill",           {"gap_atr_mult": 0.3, "atr_window": 14},                                                       "v6"),
    (35, "NQ", 30,  "ma_slope_regime",              {"ma_win": 15, "slope_bars": 3, "entry_rsi_win": 14, "rsi_ob": 60, "rsi_os": 40},             "v8"),
    (36, "NQ", 15,  "inside_bar_breakout",          {"n_inside": 2, "breakout_confirm": 0},                                                        "v8"),
    (37, "NQ", 30,  "vwap_mean_reversion",          {"z_thresh": 2.5, "vwap_window": 40},                                                          "v6"),
    (38, "ES", 30,  "vwap_mean_reversion",          {"z_thresh": 2.5, "vwap_window": 10},                                                          "v6"),
]

# ══════════════════════════════════════════════════════════════════════════════
# Run backtest for one strategy, return trades DataFrame
# ══════════════════════════════════════════════════════════════════════════════
def run_one(strat_id, symbol, tf_m, strat_name, params, version):
    key = f"{symbol}/{strat_name}/{tf_m}m"
    fn = resolve_strategy(strat_name, version)
    if fn is None:
        raise ValueError(f"Strategy '{strat_name}' not found in {version} map")

    bars = load_bars(symbol, tf_m)
    signals = fn(bars, **params)
    trades = run_backtest(bars, signals, symbol)
    if trades.empty:
        return trades

    # Ensure entry_time is UTC-aware datetime
    if "entry_time" in trades.columns:
        if trades["entry_time"].dt.tz is None:
            trades["entry_time"] = trades["entry_time"].dt.tz_localize("UTC")
        else:
            trades["entry_time"] = trades["entry_time"].dt.tz_convert("UTC")

    return trades


# ══════════════════════════════════════════════════════════════════════════════
# PART A — Session filter analysis (V678 only: IDs 16–38)
# ══════════════════════════════════════════════════════════════════════════════
def session_filter_analysis(portfolio):
    print("\n" + "="*60)
    print("PART A: Session filter analysis (IDs 16–38)")
    print("="*60)

    results = {}
    v678 = [(sid, sym, tf, sn, p, ver) for (sid, sym, tf, sn, p, ver) in portfolio if sid >= 16]

    for (sid, symbol, tf_m, strat_name, params, version) in v678:
        key = f"{symbol}/{strat_name}/{tf_m}m"
        print(f"\n  ID {sid}: {key} [{version}] ...", end=" ", flush=True)

        try:
            trades = run_one(sid, symbol, tf_m, strat_name, params, version)
        except Exception as e:
            print(f"FAILED: {e}")
            results[str(sid)] = {"key": key, "error": str(e)}
            continue

        if trades.empty:
            print("NO TRADES")
            results[str(sid)] = {"key": key, "n_trades_total": 0, "error": "no trades"}
            continue

        n_total = len(trades)
        baseline_sharpe = compute_sharpe(trades)

        # UTC entry hour
        trades = trades.copy()
        trades["utc_hour"] = trades["entry_time"].dt.hour

        # Per-hour breakdown
        per_hour = {}
        hours_to_avoid = []
        for hr, grp in trades.groupby("utc_hour"):
            n_hr      = len(grp)
            sum_pnl   = float(grp["dollar_pnl"].sum())
            mean_pnl  = float(grp["dollar_pnl"].mean())
            per_hour[str(hr)] = {"n": n_hr, "sum_pnl": round(sum_pnl, 2), "mean_pnl": round(mean_pnl, 2)}
            if sum_pnl < 0 and n_hr >= 3:
                hours_to_avoid.append(hr)

        # Filtered backtest
        if hours_to_avoid:
            mask = ~trades["utc_hour"].isin(hours_to_avoid)
            filtered_trades = trades[mask]
            filtered_sharpe = compute_sharpe(filtered_trades)
        else:
            filtered_trades = trades
            filtered_sharpe = baseline_sharpe

        if baseline_sharpe != 0:
            improvement_pct = round((filtered_sharpe - baseline_sharpe) / abs(baseline_sharpe) * 100, 2)
        else:
            improvement_pct = 0.0

        results[str(sid)] = {
            "key":                    key,
            "n_trades_total":         n_total,
            "baseline_sharpe":        round(baseline_sharpe, 4),
            "hours_to_avoid":         sorted(hours_to_avoid),
            "filtered_sharpe":        round(filtered_sharpe, 4),
            "sharpe_improvement_pct": improvement_pct,
            "per_hour":               per_hour,
        }

        print(
            f"n={n_total} | sharpe: {baseline_sharpe:.2f} -> {filtered_sharpe:.2f} "
            f"({improvement_pct:+.1f}%) | avoid hours: {sorted(hours_to_avoid)}"
        )

    return results


# ══════════════════════════════════════════════════════════════════════════════
# PART B — Combined portfolio backtest (all 38 strategies)
# ══════════════════════════════════════════════════════════════════════════════
def combined_portfolio_backtest(portfolio):
    print("\n" + "="*60)
    print("PART B: Combined portfolio backtest (all IDs 1–38)")
    print("="*60)

    all_trades = []
    strategy_summaries = {}

    for (sid, symbol, tf_m, strat_name, params, version) in portfolio:
        key = f"{symbol}/{strat_name}/{tf_m}m"
        print(f"  ID {sid:2d}: {key} [{version}] ...", end=" ", flush=True)

        try:
            trades = run_one(sid, symbol, tf_m, strat_name, params, version)
        except Exception as e:
            print(f"FAILED: {e}")
            strategy_summaries[str(sid)] = {"key": key, "error": str(e)}
            continue

        if trades.empty:
            print("no trades")
            strategy_summaries[str(sid)] = {"key": key, "n_trades": 0, "total_pnl": 0.0}
            continue

        n = len(trades)
        total_pnl = float(trades["dollar_pnl"].sum())
        sharpe    = compute_sharpe(trades)
        strategy_summaries[str(sid)] = {
            "key":       key,
            "n_trades":  n,
            "total_pnl": round(total_pnl, 2),
            "sharpe":    round(sharpe, 4),
        }
        trades["strategy_id"]  = sid
        trades["strategy_key"] = key
        all_trades.append(trades)
        print(f"n={n} | pnl=${total_pnl:,.0f} | sharpe={sharpe:.2f}")

    if not all_trades:
        print("No trades at all — cannot compute portfolio metrics.")
        return {"error": "no trades", "strategy_summaries": strategy_summaries}

    combined = pd.concat(all_trades, ignore_index=True)

    # Daily portfolio P&L (sum across all strategies each day)
    combined["date"] = combined["entry_time"].dt.date
    daily_pnl = combined.groupby("date")["dollar_pnl"].sum()

    # Portfolio Sharpe (annualised)
    port_sharpe = float(daily_pnl.mean() / daily_pnl.std() * np.sqrt(252)) if daily_pnl.std() > 0 else 0.0

    # Max drawdown
    cum_pnl    = daily_pnl.cumsum()
    running_max = cum_pnl.cummax()
    drawdowns   = running_max - cum_pnl
    max_dd      = float(drawdowns.max())

    # % positive months
    monthly_pnl  = daily_pnl.resample("ME").sum() if hasattr(daily_pnl.index, 'freq') else \
                   combined.set_index("entry_time")["dollar_pnl"].resample("ME").sum()
    pct_pos_months = float((monthly_pnl > 0).sum() / len(monthly_pnl) * 100) if len(monthly_pnl) > 0 else 0.0

    total_pnl    = float(daily_pnl.sum())
    n_days       = len(daily_pnl)
    n_pos_days   = int((daily_pnl > 0).sum())
    pct_pos_days = round(n_pos_days / n_days * 100, 1) if n_days > 0 else 0.0

    # Daily series as dict
    daily_series = {str(d): round(float(v), 2) for d, v in daily_pnl.items()}

    print("\n  -- Portfolio Summary --")
    print(f"  Total P&L:        ${total_pnl:,.0f}")
    print(f"  Portfolio Sharpe: {port_sharpe:.2f}")
    print(f"  Max Drawdown:     ${max_dd:,.0f}")
    print(f"  % Positive months:{pct_pos_months:.1f}%")
    print(f"  Trading days:     {n_days} ({pct_pos_days}% positive)")

    return {
        "summary": {
            "total_pnl":         round(total_pnl, 2),
            "portfolio_sharpe":  round(port_sharpe, 4),
            "max_drawdown":      round(max_dd, 2),
            "pct_positive_months": round(pct_pos_months, 2),
            "n_trading_days":    n_days,
            "pct_positive_days": pct_pos_days,
            "strategies_run":    len(strategy_summaries),
        },
        "strategy_summaries": strategy_summaries,
        "daily_pnl": daily_series,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    out_dir = SCRIPT_DIR

    print("\nLoading strategy modules...")
    part_a = session_filter_analysis(PORTFOLIO)

    # Save Part A
    out_a = out_dir / "session_filter_analysis.json"
    with open(out_a, "w", encoding="utf-8") as f:
        json.dump(part_a, f, indent=2, default=str)
    print(f"\n[Part A saved] {out_a}")

    part_b = combined_portfolio_backtest(PORTFOLIO)

    # Save Part B
    out_b = out_dir / "combined_portfolio_backtest.json"
    with open(out_b, "w", encoding="utf-8") as f:
        json.dump(part_b, f, indent=2, default=str)
    print(f"[Part B saved] {out_b}")

    print("\nDone.")
