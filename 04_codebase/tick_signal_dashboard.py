"""
tick_signal_dashboard.py — Live Signal & Risk Dashboard
========================================================
Reads today's signal log and current state, shows:
  - Signals fired today vs expected weekly rate
  - Per-strategy win/loss and daily P&L
  - Portfolio risk utilization (daily loss, trailing DD)
  - Active positions and halted strategies
  - Contract expiry warnings

Run:
  python tick_signal_dashboard.py              # single snapshot
  python tick_signal_dashboard.py --watch 30  # refresh every 30s
  python tick_signal_dashboard.py --days 7    # show last 7 days
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

ROOT    = Path(__file__).parent.parent
LOG_DIR = ROOT / "06_live_trading" / "logs"
STATE_DIR = ROOT / "06_live_trading" / "state"

# Expected weekly signal counts per strategy (from backtests, approximate)
_EXPECTED_WEEKLY = {
    1:  5,   # GC/obi_threshold/1m        — high frequency
    2:  3,   # ES/cvd_divergence_large_print/15m
    3:  4,   # ES/cvd_divergence/15m
    4:  3,   # ES/tape_absorption/15m
    5:  1,   # NQ/cvd_divergence_large_print/30m
    6:  4,   # NQ/stop_hunt_reversal/3m
    7:  3,   # ES/prev_session_sweep/3m
    8:  2,   # NQ/range_contraction_break/30m
    9:  4,   # GC/session_momentum_follow/3m
    10: 2,   # GC/trade_absorption_signal/30m
    11: 2,   # ES/avg_order_size_divergence/30m
    12: 1,   # NQ/trade_absorption_signal/30m
    13: 2,   # ES/key_level_cvd_rejection/15m
    14: 2,   # NQ/key_level_cvd_rejection/15m
    39: 0,   # FOMC drift — ~5/year
}


def _log_paths(n_days: int = 1) -> list[Path]:
    paths = []
    for d in range(n_days):
        dt = datetime.now(timezone.utc) - timedelta(days=d)
        p  = LOG_DIR / f"signals_{dt.strftime('%Y%m%d')}.jsonl"
        if p.exists():
            paths.append(p)
    return paths


def _load_signals(n_days: int = 1) -> list[dict]:
    records = []
    for path in _log_paths(n_days):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
    return records


def _load_state() -> dict:
    """Load executor state files."""
    state = {}
    for name in ("heartbeat.json", "last_seen_bar.json"):
        p = STATE_DIR / name
        if p.exists():
            try:
                state[name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return state


def _summarise_signals(records: list[dict]) -> dict:
    """Aggregate signal records by strategy."""
    by_strat: dict[int, dict] = defaultdict(lambda: {
        "fired": 0, "entries": 0, "exits": 0,
        "pnl_sum": 0.0, "pnl_count": 0,
        "last_action": None, "last_time": None,
        "halted": False,
    })

    for r in records:
        sid     = r.get("strategy_id", 0)
        action  = r.get("action", r.get("type", ""))
        pnl     = r.get("pnl", r.get("dollar_pnl"))
        ts      = r.get("alert_time", r.get("bar_time", ""))

        by_strat[sid]["fired"] += 1
        if action in ("BUY", "SELL"):
            by_strat[sid]["entries"] += 1
        if action in ("CLOSE", "EXIT", "TIMEOUT"):
            by_strat[sid]["exits"] += 1
            if pnl is not None:
                by_strat[sid]["pnl_sum"]   += float(pnl)
                by_strat[sid]["pnl_count"] += 1
        if action == "HALTED":
            by_strat[sid]["halted"] = True
        if ts and (by_strat[sid]["last_time"] is None or ts > by_strat[sid]["last_time"]):
            by_strat[sid]["last_action"] = action
            by_strat[sid]["last_time"]   = ts

    return dict(by_strat)


def _portfolio_daily_pnl(records: list[dict]) -> dict[str, float]:
    """Sum closed-trade P&L by date."""
    by_date: dict[str, float] = defaultdict(float)
    for r in records:
        pnl    = r.get("pnl", r.get("dollar_pnl"))
        action = r.get("action", r.get("type", ""))
        if action not in ("CLOSE", "EXIT", "TIMEOUT"):
            continue
        if pnl is None:
            continue
        ts = r.get("alert_time", r.get("bar_time", ""))
        if ts:
            date = ts[:10]
            by_date[date] += float(pnl)
    return dict(by_date)


def _render(n_days: int = 1) -> str:
    now   = datetime.now(timezone.utc)
    lines = [
        "=" * 64,
        f"  FORTRESS SIGNAL DASHBOARD — {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "=" * 64,
    ]

    records = _load_signals(n_days)
    state   = _load_state()
    summary = _summarise_signals(records)
    daily   = _portfolio_daily_pnl(records)

    # ── Heartbeat ──────────────────────────────────────────────────────────────
    hb = state.get("heartbeat.json", {})
    if hb:
        last_hb = hb.get("last_check", hb.get("ts", "?"))
        mode    = hb.get("mode", "?")
        lines.append(f"\n  Executor: mode={mode}  last_check={last_hb}")
    else:
        lines.append("\n  Executor: not running (no heartbeat)")

    # ── Portfolio daily P&L ────────────────────────────────────────────────────
    lines.append(f"\n{'─' * 64}")
    lines.append("  DAILY P&L (closed trades)")
    lines.append(f"{'─' * 64}")
    if daily:
        for date in sorted(daily.keys(), reverse=True)[:n_days]:
            pnl   = daily[date]
            sign  = "+" if pnl >= 0 else ""
            lines.append(f"  {date}   {sign}${pnl:,.2f}")
    else:
        lines.append("  No closed trades in log period")

    total = sum(daily.values())
    if len(daily) > 1:
        sign = "+" if total >= 0 else ""
        lines.append(f"  {'─'*30}")
        lines.append(f"  Total ({n_days}d)       {sign}${total:,.2f}")

    # ── Per-strategy breakdown ─────────────────────────────────────────────────
    lines.append(f"\n{'─' * 64}")
    lines.append(f"  STRATEGY ACTIVITY (last {n_days} day{'s' if n_days > 1 else ''})")
    lines.append(f"{'─' * 64}")
    lines.append(f"  {'ID':>3}  {'Entries':>7}  {'Exits':>6}  {'P&L':>9}  {'Halted':>7}  Last")

    for sid in sorted(summary.keys()):
        s    = summary[sid]
        pnl  = s["pnl_sum"] if s["pnl_count"] > 0 else None
        pnl_s = (f"+${pnl:,.2f}" if pnl and pnl >= 0 else f"-${abs(pnl):,.2f}") if pnl is not None else "—"
        halt  = "HALT" if s["halted"] else "ok"
        last  = (s["last_time"] or "")[-8:] if s["last_time"] else "—"
        lines.append(f"  {sid:>3}  {s['entries']:>7}  {s['exits']:>6}  {pnl_s:>9}  {halt:>7}  {last}")

    if not summary:
        lines.append("  No signals in log period")

    # ── Signal rate check ──────────────────────────────────────────────────────
    if n_days >= 7:
        lines.append(f"\n{'─' * 64}")
        lines.append("  SIGNAL RATE vs EXPECTED (7-day)")
        lines.append(f"{'─' * 64}")
        any_anomaly = False
        for sid, expected_wk in sorted(_EXPECTED_WEEKLY.items()):
            actual = summary.get(sid, {}).get("entries", 0)
            ratio  = actual / expected_wk if expected_wk > 0 else None
            if ratio is not None:
                flag = ""
                if ratio < 0.3:
                    flag = "  <-- LOW (possible data gap)"
                    any_anomaly = True
                elif ratio > 3.0:
                    flag = "  <-- HIGH (check for false signals)"
                    any_anomaly = True
                lines.append(f"  ID {sid:>2}: {actual:>3} fired  (expected ~{expected_wk}/wk){flag}")
        if not any_anomaly:
            lines.append("  All rates nominal.")

    # ── Contract expiry warnings ───────────────────────────────────────────────
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from tick_live_executor import TV_CONTRACT_MAP, _CONTRACT_EXPIRY
        expiry_warns = []
        today = now.date()
        for base, tv_sym in TV_CONTRACT_MAP.items():
            expiry_str = _CONTRACT_EXPIRY.get(tv_sym)
            if not expiry_str:
                continue
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            days = (expiry_date - today).days
            if days <= 21:
                flag = "***URGENT***" if days <= 7 else "WARNING"
                expiry_warns.append(f"  {flag}  {tv_sym} expires {expiry_str} ({days} days)")
        if expiry_warns:
            lines.append(f"\n{'─' * 64}")
            lines.append("  CONTRACT EXPIRY")
            lines.append(f"{'─' * 64}")
            lines.extend(expiry_warns)
            if any(days <= 7 for days in
                   [(datetime.strptime(_CONTRACT_EXPIRY.get(v,"2099-01-01"),"%Y-%m-%d").date()-today).days
                    for v in TV_CONTRACT_MAP.values() if _CONTRACT_EXPIRY.get(v)]):
                lines.append("  ACTION: run tick_contract_rollover.py --to U5")
    except Exception:
        pass

    lines.append(f"\n{'=' * 64}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Fortress signal dashboard")
    parser.add_argument("--watch", type=int, metavar="SECONDS",
                        help="Refresh interval in seconds (default: single snapshot)")
    parser.add_argument("--days", type=int, default=1,
                        help="Number of days of log history to show (default: 1)")
    args = parser.parse_args()

    if args.watch:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            print(_render(args.days))
            time.sleep(args.watch)
    else:
        print(_render(args.days))


if __name__ == "__main__":
    main()
