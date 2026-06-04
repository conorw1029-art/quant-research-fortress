"""
tick_degradation_report.py — Live vs Backtest Degradation Report
================================================================
Reads the live signal log (JSONL) and compares actual live metrics
against backtest expectations for each strategy.

Produces a clear table showing:
  - Signals fired live vs expected rate
  - Win rate (if trade outcomes are tracked)
  - Signal frequency deviation
  - Any strategies firing suspiciously rarely or often (regime shift warning)

Usage:
  python 04_codebase/tick_degradation_report.py
  python 04_codebase/tick_degradation_report.py --days 30
  python 04_codebase/tick_degradation_report.py --output report.txt
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT    = Path(__file__).parent.parent
LOG_DIR = ROOT / "06_live_trading" / "logs"

# Backtest expectations per strategy (from walk-forward results)
# Format: strategy_id -> {name, symbol, trades_per_year_backtest, win_rate_backtest, sharpe_backtest}
BACKTEST_STATS: dict[int, dict] = {
    1:  {"name": "cvd_divergence_large_print", "symbol": "NQ",  "trades_yr": 28,   "wr": 0.54, "sharpe": 2.1},
    2:  {"name": "cvd_divergence_large_print", "symbol": "ES",  "trades_yr": 45,   "wr": 0.52, "sharpe": 1.8},
    3:  {"name": "stop_hunt_reversal",         "symbol": "NQ",  "trades_yr": 120,  "wr": 0.55, "sharpe": 2.3},
    4:  {"name": "obi_threshold",              "symbol": "GC",  "trades_yr": 340,  "wr": 0.51, "sharpe": 3.2},
    5:  {"name": "tape_absorption",            "symbol": "ES",  "trades_yr": 38,   "wr": 0.58, "sharpe": 2.0},
    6:  {"name": "cvd_divergence",             "symbol": "ES",  "trades_yr": 90,   "wr": 0.52, "sharpe": 1.9},
    7:  {"name": "prev_session_sweep",         "symbol": "ES",  "trades_yr": 85,   "wr": 0.56, "sharpe": 1.5},
    8:  {"name": "range_contraction_break",    "symbol": "NQ",  "trades_yr": 62,   "wr": 0.54, "sharpe": 5.6},
    9:  {"name": "session_momentum_follow",    "symbol": "GC",  "trades_yr": 95,   "wr": 0.51, "sharpe": 3.2},
    10: {"name": "trade_absorption_signal",    "symbol": "GC",  "trades_yr": 42,   "wr": 0.52, "sharpe": 4.7},
    11: {"name": "avg_order_size_divergence",  "symbol": "ES",  "trades_yr": 55,   "wr": 0.50, "sharpe": 1.0},
    12: {"name": "trade_absorption_signal",    "symbol": "NQ",  "trades_yr": 21,   "wr": 0.57, "sharpe": 6.5},
    16: {"name": "vwap_mean_reversion",        "symbol": "GC",  "trades_yr": 88,   "wr": 0.55, "sharpe": 2.7},
    17: {"name": "pivot_reversal",             "symbol": "GC",  "trades_yr": 65,   "wr": 0.53, "sharpe": 2.0},
    18: {"name": "opening_range_fakeout",      "symbol": "SI",  "trades_yr": 72,   "wr": 0.54, "sharpe": 2.5},
    19: {"name": "consecutive_close_momentum", "symbol": "SI",  "trades_yr": 210,  "wr": 0.52, "sharpe": 2.3},
    20: {"name": "pivot_reversal",             "symbol": "GC",  "trades_yr": 78,   "wr": 0.52, "sharpe": 1.9},
    21: {"name": "ema_crossover",              "symbol": "SI",  "trades_yr": 290,  "wr": 0.51, "sharpe": 1.8},
    22: {"name": "vwap_mean_reversion",        "symbol": "SI",  "trades_yr": 95,   "wr": 0.52, "sharpe": 1.8},
    23: {"name": "opening_range_fakeout",      "symbol": "SI",  "trades_yr": 180,  "wr": 0.53, "sharpe": 1.5},
    42: {"name": "CVD_Microprice",             "symbol": "SI",  "trades_yr": 209,  "wr": 0.52, "sharpe": 2.5},
    43: {"name": "CVD_Acceleration",           "symbol": "GC",  "trades_yr": 180,  "wr": 0.50, "sharpe": 2.1},
    44: {"name": "Repeated_Replenishment",     "symbol": "GC",  "trades_yr": 723,  "wr": 0.51, "sharpe": 4.4},
}


def load_signal_log(days: int = 30) -> list[dict]:
    """Load accepted signals from the last N days."""
    log_path = LOG_DIR / "signal_log.jsonl"
    if not log_path.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    signals = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if not rec.get("accepted"):
                        continue
                    ts_str = rec.get("timestamp", "")
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts >= cutoff:
                        signals.append(rec)
                except Exception:
                    continue
    except Exception as e:
        print(f"ERROR reading signal log: {e}")
    return signals


def build_report(days: int = 30) -> str:
    signals = load_signal_log(days)

    if not signals:
        return (
            f"No live signals found in last {days} days.\n"
            f"  Log file: {LOG_DIR / 'signal_log.jsonl'}\n"
            f"  Make sure the executor has been running and firing signals."
        )

    # Group by strategy_id
    by_strat: dict[int, list] = {}
    for s in signals:
        sid = int(s.get("strategy_id", 0))
        by_strat.setdefault(sid, []).append(s)

    lines = []
    lines.append("=" * 72)
    lines.append(f"  LIVE vs BACKTEST DEGRADATION REPORT  ({days}-day window)")
    lines.append(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"  Total accepted signals: {len(signals)}")
    lines.append("=" * 72)
    lines.append("")

    # Header
    lines.append(f"{'ID':>3}  {'Strategy':<28} {'Sym':>4}  "
                 f"{'Live/day':>8}  {'Exp/day':>8}  {'Ratio':>6}  {'Status':<12}")
    lines.append("-" * 72)

    all_sids = sorted(set(list(by_strat.keys()) + list(BACKTEST_STATS.keys())))
    warnings = []

    for sid in all_sids:
        live_signals = by_strat.get(sid, [])
        n_live  = len(live_signals)
        per_day = n_live / days if days > 0 else 0

        bt  = BACKTEST_STATS.get(sid)
        if bt:
            name        = bt["name"][:28]
            symbol      = bt["symbol"]
            exp_per_day = bt["trades_yr"] / 252
            ratio       = per_day / exp_per_day if exp_per_day > 0 else 0

            if n_live == 0:
                status = "NO SIGNALS"
                warnings.append(f"  Strategy {sid} ({name}): 0 live signals in {days}d (expected {exp_per_day:.2f}/day)")
            elif ratio < 0.3:
                status = "VERY LOW"
                warnings.append(f"  Strategy {sid} ({name}): {ratio:.2f}x backtest rate — possible regime shift or data issue")
            elif ratio < 0.6:
                status = "LOW"
            elif ratio > 3.0:
                status = "VERY HIGH"
                warnings.append(f"  Strategy {sid} ({name}): {ratio:.2f}x backtest rate — possible overfitting or data error")
            elif ratio > 1.8:
                status = "HIGH"
            else:
                status = "OK"

            lines.append(f"{sid:>3}  {name:<28} {symbol:>4}  "
                         f"{per_day:>8.2f}  {exp_per_day:>8.2f}  {ratio:>6.2f}  {status:<12}")
        else:
            lines.append(f"{sid:>3}  {'(unknown strategy)':<28} {'?':>4}  "
                         f"{per_day:>8.2f}  {'?':>8}  {'?':>6}  {'NO BASELINE':<12}")

    lines.append("")
    lines.append(f"Strategies with live signals: {len(by_strat)}")
    lines.append(f"Strategies silent:            {len([s for s in BACKTEST_STATS if s not in by_strat])}")

    if warnings:
        lines.append("")
        lines.append("WARNINGS:")
        lines.extend(warnings)
    else:
        lines.append("")
        lines.append("No degradation warnings. Signal rates look normal.")

    lines.append("")
    lines.append("NOTE: Win rate comparison requires broker trade outcomes.")
    lines.append("      Wire broker reconciliation to get filled/stopped data.")
    lines.append("=" * 72)

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Live vs backtest degradation report")
    parser.add_argument("--days",   type=int, default=30, help="Lookback window in days")
    parser.add_argument("--output", type=str, default=None, help="Write report to file")
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    report = build_report(days=args.days)
    print(report)

    if args.output:
        out = Path(args.output)
        out.write_text(report, encoding="utf-8")
        print(f"\nReport saved to: {out}")


if __name__ == "__main__":
    main()
