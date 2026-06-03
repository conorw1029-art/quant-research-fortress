"""
tick_daily_signal_report.py
============================
Generates a daily performance report from JSONL signal logs.

Usage:
    python tick_daily_signal_report.py
    python tick_daily_signal_report.py --date 2026-06-03
    python tick_daily_signal_report.py --date 2026-06-03 --send-telegram

Reads:  06_live_trading/logs/signals_YYYYMMDD.jsonl
Writes: 06_live_trading/reports/daily_YYYYMMDD.json
Prints: formatted summary to console
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("DailyReport")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR    = _REPO_ROOT / "06_live_trading" / "logs"
REPORT_DIR = _REPO_ROOT / "06_live_trading" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ===========================================================================
# Log reader
# ===========================================================================

def read_signals_jsonl(date_str: str) -> List[Dict[str, Any]]:
    """
    Read all signal records for a given date (YYYY-MM-DD).
    Returns list of dicts. Empty list if file not found.
    """
    date_compact = date_str.replace("-", "")
    path         = LOG_DIR / f"signals_{date_compact}.jsonl"

    if not path.exists():
        logger.warning(f"Signal log not found: {path}")
        return []

    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning(f"JSON parse error on line {lineno}: {e}")
    logger.info(f"Loaded {len(records)} records from {path.name}")
    return records


# ===========================================================================
# Report computation
# ===========================================================================

def compute_report(records: List[Dict[str, Any]], date_str: str) -> Dict[str, Any]:
    """
    Compute all report metrics from a list of signal records.

    Returns a report dict with keys matching the design spec.
    """
    fired_signals   = [r for r in records if not r.get("is_blocked", False) and r.get("side", "N/A") != "N/A"]
    blocked_signals = [r for r in records if r.get("is_blocked", False)]

    # Block reason breakdown
    block_breakdown: Dict[str, int] = defaultdict(int)
    for r in blocked_signals:
        reason = r.get("block_reason", "UNKNOWN")
        # Group by prefix (e.g., "NEWS: ...", "STALE_BAR: ...")
        prefix = reason.split(":")[0].strip() if ":" in reason else reason
        block_breakdown[prefix] += 1

    # Signals by symbol
    signals_by_symbol: Dict[str, int] = defaultdict(int)
    for r in fired_signals:
        sym = r.get("symbol", "UNKNOWN")
        signals_by_symbol[sym] += 1

    # Signals by strategy
    signals_by_strategy: Dict[str, int] = defaultdict(int)
    for r in fired_signals:
        strat = r.get("strategy_name", "UNKNOWN")
        signals_by_strategy[strat] += 1

    # Hypothetical fills — only records with resolved outcomes
    resolved = [r for r in fired_signals if r.get("hypo_outcome") in ("WIN", "LOSS")]
    wins     = [r for r in resolved if r.get("hypo_outcome") == "WIN"]
    losses   = [r for r in resolved if r.get("hypo_outcome") == "LOSS"]

    hypo_pnl      = sum(r.get("hypo_pnl_dollars", 0.0) or 0.0 for r in resolved)
    win_rate_pct  = 100.0 * len(wins) / len(resolved) if resolved else 0.0
    avg_r         = 0.0
    if resolved:
        r_vals = [r.get("hypo_r_achieved", 0.0) or 0.0 for r in resolved]
        avg_r  = sum(r_vals) / len(r_vals)

    # Worst miss: highest potential gain among blocked signals that had a WIN outcome
    worst_miss_str: Optional[str] = None
    blocked_with_outcomes = [
        r for r in blocked_signals
        if r.get("hypo_outcome") == "WIN" and r.get("hypo_pnl_dollars") is not None
    ]
    if blocked_with_outcomes:
        best_blocked = max(blocked_with_outcomes, key=lambda r: r.get("hypo_pnl_dollars", 0.0))
        worst_miss_str = (
            f"{best_blocked.get('strategy_name')} | {best_blocked.get('symbol')} | "
            f"{best_blocked.get('side')} | "
            f"Would have earned ${best_blocked.get('hypo_pnl_dollars', 0):.0f} "
            f"({best_blocked.get('hypo_r_achieved', 0):.2f}R) | "
            f"Blocked: {best_blocked.get('block_reason', '?')}"
        )

    # Largest single winner / loser
    best_trade  = max(resolved, key=lambda r: r.get("hypo_pnl_dollars", 0.0), default=None) if resolved else None
    worst_trade = min(resolved, key=lambda r: r.get("hypo_pnl_dollars", 0.0), default=None) if resolved else None

    def _trade_summary(r: Optional[Dict[str, Any]]) -> Optional[str]:
        if r is None:
            return None
        return (
            f"{r.get('strategy_name')} | {r.get('symbol')} | {r.get('side')} | "
            f"${r.get('hypo_pnl_dollars', 0):.0f} | {r.get('hypo_r_achieved', 0):.2f}R"
        )

    # Open (unresolved) signals
    open_signals = [r for r in fired_signals if r.get("hypo_outcome") is None or r.get("hypo_outcome") == "OPEN"]

    return {
        "date":                    date_str,
        "generated_at":            datetime.now(timezone.utc).isoformat(),
        "total_signals_fired":     len(fired_signals),
        "total_signals_blocked":   len(blocked_signals),
        "block_breakdown":         dict(block_breakdown),
        "signals_by_symbol":       dict(signals_by_symbol),
        "signals_by_strategy":     dict(signals_by_strategy),
        "open_signals":            len(open_signals),
        "resolved_signals":        len(resolved),
        "wins":                    len(wins),
        "losses":                  len(losses),
        "hypo_pnl_dollars":        round(hypo_pnl, 2),
        "hypo_win_rate_pct":       round(win_rate_pct, 1),
        "avg_r_achieved":          round(avg_r, 3),
        "best_trade_summary":      _trade_summary(best_trade),
        "worst_trade_summary":     _trade_summary(worst_trade),
        "worst_miss":              worst_miss_str,
        "raw_fired_count":         len(fired_signals),
        "raw_blocked_count":       len(blocked_signals),
    }


# ===========================================================================
# Console formatter
# ===========================================================================

def print_report(report: Dict[str, Any]) -> None:
    """Print formatted report to console."""
    sep  = "=" * 62
    sep2 = "-" * 62

    date   = report.get("date", "?")
    gen_at = report.get("generated_at", "")[:19] + " UTC"

    print(f"\n{sep}")
    print(f"  DAILY SIGNAL REPORT — {date}")
    print(f"  Generated: {gen_at}")
    print(sep)

    print(f"  Signals Fired:   {report['total_signals_fired']}")
    print(f"  Signals Blocked: {report['total_signals_blocked']}")
    block_brkd = report.get("block_breakdown", {})
    if block_brkd:
        for reason, count in block_brkd.items():
            print(f"    [{reason}]: {count}")

    print(sep2)
    print(f"  HYPOTHETICAL PERFORMANCE  ({report['resolved_signals']} resolved signals)")
    print(f"    Total PnL:  ${report['hypo_pnl_dollars']:,.2f}")
    print(f"    Win Rate:   {report['hypo_win_rate_pct']:.1f}%  "
          f"({report['wins']}W / {report['losses']}L)")
    print(f"    Avg R:      {report['avg_r_achieved']:.2f}R")
    print(f"    Open:       {report['open_signals']} signal(s) still pending")

    best  = report.get("best_trade_summary")
    worst = report.get("worst_trade_summary")
    if best:
        print(f"    Best Trade: {best}")
    if worst:
        print(f"    Worst Trade:{worst}")

    by_sym = report.get("signals_by_symbol", {})
    if by_sym:
        print(sep2)
        print("  By Symbol:")
        for sym, cnt in sorted(by_sym.items()):
            print(f"    {sym}: {cnt} signal(s)")

    by_strat = report.get("signals_by_strategy", {})
    if by_strat:
        print("  By Strategy:")
        for strat, cnt in sorted(by_strat.items()):
            print(f"    {strat}: {cnt} signal(s)")

    worst_miss = report.get("worst_miss")
    if worst_miss:
        print(sep2)
        print("  WORST MISS (blocked signal that would have won):")
        print(f"    {worst_miss}")

    print(sep)
    print()


# ===========================================================================
# Telegram summary
# ===========================================================================

def send_telegram_summary(report: Dict[str, Any]) -> bool:
    """Send condensed summary via Telegram if env vars are set."""
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment")
        return False

    date     = report.get("date", "?")
    total    = report.get("total_signals_fired", 0)
    pnl      = report.get("hypo_pnl_dollars", 0.0)
    win_rate = report.get("hypo_win_rate_pct", 0.0)
    avg_r    = report.get("avg_r_achieved", 0.0)
    wins     = report.get("wins", 0)
    losses   = report.get("losses", 0)
    blocked  = report.get("total_signals_blocked", 0)

    text = (
        f"Daily Signal Report — {date}\n"
        f"Signals Fired: {total} | Blocked: {blocked}\n"
        f"Hypo PnL: ${pnl:,.0f}\n"
        f"Win Rate: {win_rate:.1f}%  ({wins}W / {losses}L)\n"
        f"Avg R: {avg_r:.2f}R"
    )

    try:
        import requests  # type: ignore
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        if resp.status_code == 200:
            logger.info("Telegram summary sent successfully")
            return True
        else:
            logger.error(f"Telegram error {resp.status_code}: {resp.text[:200]}")
            return False
    except ImportError:
        logger.error("requests library not available — cannot send Telegram message")
        return False
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


# ===========================================================================
# Write report JSON
# ===========================================================================

def write_report(report: Dict[str, Any], date_str: str) -> Path:
    """Write report dict to JSON file. Returns path."""
    date_compact = date_str.replace("-", "")
    path         = REPORT_DIR / f"daily_{date_compact}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info(f"Report written to {path}")
    return path


# ===========================================================================
# CLI
# ===========================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate daily signal performance report from JSONL logs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--date", default=None,
        help="Date to report (YYYY-MM-DD). Default: today UTC",
    )
    parser.add_argument(
        "--send-telegram", action="store_true",
        help="Send summary to Telegram (requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars)",
    )
    parser.add_argument(
        "--no-write", action="store_true",
        help="Do not write report JSON to disk (console output only)",
    )
    return parser.parse_args()


def main() -> None:
    args     = parse_args()
    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    logger.info(f"Generating daily report for {date_str}")

    records = read_signals_jsonl(date_str)
    if not records:
        print(f"No signal records found for {date_str}. Nothing to report.")
        if not args.no_write:
            # Write empty report so the file exists
            empty = compute_report([], date_str)
            write_report(empty, date_str)
        sys.exit(0)

    report = compute_report(records, date_str)

    print_report(report)

    if not args.no_write:
        write_report(report, date_str)

    if args.send_telegram:
        ok = send_telegram_summary(report)
        if not ok:
            logger.warning("Telegram summary failed — check env vars and token")


if __name__ == "__main__":
    main()
