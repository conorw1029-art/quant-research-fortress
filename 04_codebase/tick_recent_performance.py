"""
tick_recent_performance.py -- Past-month strategy performance report
====================================================================
Runs all portfolio strategies against the most recent N days of bar data
and reports per-strategy and portfolio P&L in MICRO-contract terms (1/10th
of the full-contract backtest output -- matches actual trading size).

Usage:
  python tick_recent_performance.py               # last 30 days
  python tick_recent_performance.py --days 60     # last 60 days
  python tick_recent_performance.py --survivors   # 5 hardened survivors only
  python tick_recent_performance.py --csv out.csv # dump trade log to CSV
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from tick_backtest_engine import run_backtest, compute_metrics, SPECS
from tick_strategies    import STRATEGY_MAP
from tick_strategies_v2 import STRAT_MAP
from tick_strategies_v3 import STRAT_MAP_V3
from tick_strategies_v4 import STRAT_MAP_V4
try:
    from tick_strategies_v5 import STRAT_MAP_V5
except Exception:
    STRAT_MAP_V5 = {}

ROOT    = Path(__file__).parent.parent
BAR_DIR = ROOT / "01_data" / "tick_bars"

MICRO_FACTOR = 0.1   # micro contracts are 1/10 full-contract dollar value

# Full portfolio -- mirrors PORTFOLIO in tick_live_executor.py
# (strat_id, base_symbol, bar_min, strat_name, params, version, survivor)
PORTFOLIO = [
    (1,  "GC",  1,  "obi_threshold",                {"threshold": 0.3, "smooth_window": 1},
     "v1", False),
    (2,  "ES", 15,  "cvd_divergence_large_print",   {"price_window": 20, "cvd_window": 10, "min_large": 1},
     "v1", True),
    (3,  "ES", 15,  "cvd_divergence",               {"price_window": 40, "cvd_window": 20, "threshold": 0.3},
     "v1", False),
    (4,  "ES", 15,  "tape_absorption",              {"price_window": 5, "vol_z_threshold": 1.5, "price_threshold": 0.001},
     "v1", False),
    (5,  "NQ", 30,  "cvd_divergence_large_print",   {"price_window": 20, "cvd_window": 10, "min_large": 2},
     "v1", False),
    (6,  "NQ",  3,  "stop_hunt_reversal",           {"spike_bars": 1, "spike_pct": 0.001, "cvd_flip_window": 5},
     "v1", False),
    (7,  "ES",  3,  "prev_session_sweep",           {"level_window": 20, "cvd_flip_window": 3, "sweep_buffer": 0.0001},
     "v2", True),
    (8,  "NQ", 30,  "range_contraction_break",      {"squeeze_pct": 30, "breakout_z": 1.0, "cvd_z": 0.5},
     "v3", True),
    (9,  "GC",  3,  "session_momentum_follow",      {"bias_z": 1.0, "follow_bars": 8, "break_pct": 0.0002},
     "v3", True),
    (10, "GC", 30,  "trade_absorption_signal",      {"ntrades_z": 1.2, "range_z": -0.3, "cvd_z": 0.4},
     "v4", True),
    (11, "ES", 30,  "avg_order_size_divergence",    {"window": 20, "z_thresh": 1.0, "price_thresh": 0.001},
     "v4", False),
    (12, "NQ", 30,  "trade_absorption_signal",      {"ntrades_z": 1.2, "range_z": -0.3, "cvd_z": 0.4},
     "v4", False),
    # V5 -- key_level_cvd_rejection (added 2026-05-17)
    (13, "ES", 15,  "key_level_cvd_rejection",      {"key_level_window": 30, "cvd_window": 10, "rejection_atr_pct": 0.25},
     "v5", False),
    (14, "NQ", 15,  "key_level_cvd_rejection",      {"key_level_window": 30, "cvd_window": 20, "rejection_atr_pct": 0.5},
     "v5", False),
    (15, "GC",  5,  "key_level_cvd_rejection",      {"key_level_window": 10, "cvd_window": 10, "rejection_atr_pct": 1.0},
     "v5", False),
]

_STRAT_MAPS = {"v1": STRATEGY_MAP, "v2": STRAT_MAP, "v3": STRAT_MAP_V3, "v4": STRAT_MAP_V4, "v5": STRAT_MAP_V5}


def load_recent(symbol: str, bar_min: int, days: int) -> pd.DataFrame | None:
    path = BAR_DIR / f"{symbol}_bars_{bar_min}m.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    df.sort_index(inplace=True)
    # Use last N calendar days relative to the newest available bar
    cutoff = df.index[-1] - pd.Timedelta(days=days)
    df = df[df.index >= cutoff]
    return df if len(df) >= 20 else None


def _fmt_pnl(x: float) -> str:
    sign = "+" if x >= 0 else ""
    return f"{sign}${x:,.0f}"


def run(days: int = 30, survivors_only: bool = False, csv_path: str | None = None):
    portfolio = [p for p in PORTFOLIO if (not survivors_only or p[6])]

    print(f"\n{'='*72}")
    print(f"  PAST {days}-DAY PERFORMANCE REPORT -- MICRO CONTRACT P&L")
    print(f"  (All figures /10 vs backtest: micro = 1 contract each)")
    print(f"{'='*72}")
    print()

    all_trades   = []
    rows         = []

    for (sid, symbol, bar_min, strat_name, params, version, is_survivor) in portfolio:
        df = load_recent(symbol, bar_min, days)
        if df is None:
            print(f"  [{sid:2d}] {symbol}/{strat_name}/{bar_min}m -- NO DATA")
            rows.append({
                "id": sid, "symbol": symbol, "strategy": strat_name,
                "bar_min": bar_min, "survivor": is_survivor,
                "period_start": "--", "period_end": "--",
                "n_trades": 0, "win_rate": np.nan,
                "micro_pnl": 0.0, "micro_dd": 0.0,
                "avg_win": np.nan, "avg_loss": np.nan, "sharpe": np.nan,
            })
            continue

        strat = _STRAT_MAPS.get(version, {}).get(strat_name)
        if strat is None:
            print(f"  [{sid:2d}] {symbol}/{strat_name} -- not found in {version} map")
            continue

        try:
            signals = strat["compute"](df, **params)
        except Exception as e:
            print(f"  [{sid:2d}] {symbol}/{strat_name} -- signal error: {e}")
            continue

        trades = run_backtest(df, signals, symbol)
        period_start = df.index[0].strftime("%Y-%m-%d")
        period_end   = df.index[-1].strftime("%Y-%m-%d")

        if trades.empty:
            print(f"  [{sid:2d}] {symbol}/{strat_name}/{bar_min}m  "
                  f"({period_start} -> {period_end}) -- 0 trades")
            rows.append({
                "id": sid, "symbol": symbol, "strategy": strat_name,
                "bar_min": bar_min, "survivor": is_survivor,
                "period_start": period_start, "period_end": period_end,
                "n_trades": 0, "win_rate": 0.0,
                "micro_pnl": 0.0, "micro_dd": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "sharpe": 0.0,
            })
            continue

        m = compute_metrics(trades)
        if np.isnan(m["n_trades"]):
            n_t = len(trades)
            print(f"  [{sid:2d}] {symbol}/{strat_name}/{bar_min}m  "
                  f"({period_start} -> {period_end}) -- {n_t} trades (< 5, metrics skipped)")
            rows.append({
                "id": sid, "symbol": symbol, "strategy": strat_name,
                "bar_min": bar_min, "survivor": is_survivor,
                "period_start": period_start, "period_end": period_end,
                "n_trades": n_t, "win_rate": np.nan,
                "micro_pnl": trades["dollar_pnl"].sum() * MICRO_FACTOR, "micro_dd": 0.0,
                "avg_win": np.nan, "avg_loss": np.nan, "sharpe": np.nan,
            })
            continue
        micro_pnl = m["total_pnl"] * MICRO_FACTOR
        micro_dd  = m["max_dd"]    * MICRO_FACTOR
        micro_win = m["avg_win"]   * MICRO_FACTOR
        micro_los = m["avg_loss"]  * MICRO_FACTOR

        trades["micro_pnl"] = trades["dollar_pnl"] * MICRO_FACTOR
        trades["strat_id"]  = sid
        trades["strategy"]  = strat_name
        trades["symbol"]    = symbol
        all_trades.append(trades)

        star = " ***" if is_survivor else ""
        tag  = "[SURVIVOR]" if is_survivor else "          "
        print(f"  [{sid:2d}] {tag} {symbol}/{strat_name}/{bar_min}m{star}")
        print(f"         Period:  {period_start} -> {period_end}")
        print(f"         Trades:  {m['n_trades']}   "
              f"WR: {m['win_rate']:.0%}   "
              f"Avg W: {_fmt_pnl(micro_win)}  "
              f"Avg L: {_fmt_pnl(micro_los)}")
        print(f"         P&L:     {_fmt_pnl(micro_pnl)}   "
              f"MaxDD: -${abs(micro_dd):,.0f}   "
              f"Sharpe: {m['sharpe']:.2f}   "
              f"DSR: {m['dsr']:.2f}")
        print()

        rows.append({
            "id": sid, "symbol": symbol, "strategy": strat_name,
            "bar_min": bar_min, "survivor": is_survivor,
            "period_start": period_start, "period_end": period_end,
            "n_trades": m["n_trades"], "win_rate": m["win_rate"],
            "micro_pnl": micro_pnl, "micro_dd": micro_dd,
            "avg_win": micro_win, "avg_loss": micro_los,
            "sharpe": m["sharpe"],
        })

    # ── Portfolio summary ─────────────────────────────────────────────────────
    print(f"{'='*72}")

    if all_trades:
        combined = pd.concat(all_trades, ignore_index=True)
        combined.sort_values("exit_time", inplace=True)

        total_pnl = combined["micro_pnl"].sum()
        n_total   = len(combined)
        n_wins    = (combined["micro_pnl"] > 0).sum()
        port_wr   = n_wins / n_total if n_total else 0.0

        cum      = combined["micro_pnl"].cumsum()
        peak     = cum.cummax()
        port_dd  = (cum - peak).min()

        print(f"  Portfolio total P&L (micro): {_fmt_pnl(total_pnl)}")
        print(f"  Portfolio max drawdown (micro): -${abs(port_dd):,.0f}")
        print(f"  Total trades: {n_total}   Win rate: {port_wr:.0%}")
        print()

        # Survivor-only sub-total
        surv_trades = combined[combined["strat_id"].isin(
            [p[0] for p in PORTFOLIO if p[6]]
        )]
        if not surv_trades.empty and not survivors_only:
            surv_pnl = surv_trades["micro_pnl"].sum()
            print(f"  Survivor strategies only:   {_fmt_pnl(surv_pnl)}  "
                  f"({len(surv_trades)} trades)")

        print(f"{'='*72}")

        # Per-day P&L summary
        combined["date"] = pd.to_datetime(combined["exit_time"]).dt.date
        daily = combined.groupby("date")["micro_pnl"].sum()
        pos_days = (daily > 0).sum()
        neg_days = (daily < 0).sum()
        print(f"\n  Daily P&L summary ({len(daily)} trading days):")
        print(f"    Positive days: {pos_days}   Negative days: {neg_days}")
        print(f"    Best day:  {_fmt_pnl(daily.max())}   "
              f"Worst day: {_fmt_pnl(daily.min())}")
        print(f"    Avg day:   {_fmt_pnl(daily.mean())}")

        # Worst 3 days
        worst = daily.nsmallest(3)
        print(f"\n  Worst 3 days:")
        for dt, pnl in worst.items():
            print(f"    {dt}  {_fmt_pnl(pnl)}")

        if csv_path:
            combined.to_csv(csv_path, index=False)
            print(f"\n  Trade log saved to {csv_path}")

    else:
        print("  No trades found in the period.")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Past-month performance report")
    parser.add_argument("--days",      type=int, default=30,
                        help="Lookback period in calendar days (default: 30)")
    parser.add_argument("--survivors", action="store_true",
                        help="Only run the 5 hardened survivor strategies")
    parser.add_argument("--csv",       type=str, default=None,
                        help="Optional path to dump trade-level CSV")
    args = parser.parse_args()
    run(days=args.days, survivors_only=args.survivors, csv_path=args.csv)
