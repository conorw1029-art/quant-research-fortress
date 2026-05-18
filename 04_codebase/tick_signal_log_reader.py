"""
tick_signal_log_reader.py — Signal Log Analysis Tool
=====================================================
Parses JSONL signal logs produced by tick_live_executor.py and reports:
  - Per-strategy acceptance/rejection rates
  - Most recent signals (accepted and rejected)
  - Rejection reason breakdown
  - Daily trade count and signal frequency

Usage:
  python tick_signal_log_reader.py              # today only
  python tick_signal_log_reader.py --days 3    # last 3 days
  python tick_signal_log_reader.py --strategy 2  # filter to one strategy
  python tick_signal_log_reader.py --accepted  # only accepted signals
  python tick_signal_log_reader.py --recent 20 # show last 20 signals
  python tick_signal_log_reader.py --json      # machine-readable output
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT    = Path(__file__).parent.parent
LOG_DIR = ROOT / "06_live_trading" / "logs"

# Strategy ID → human-readable name (mirrors PORTFOLIO)
_STRAT_NAMES = {
    1:  "GC/obi_threshold/1m",
    2:  "ES/cvd_divergence_large_print/15m",
    3:  "ES/cvd_divergence/15m",
    4:  "ES/tape_absorption/15m",
    5:  "NQ/cvd_divergence_large_print/30m",
    6:  "NQ/stop_hunt_reversal/3m",
    7:  "ES/prev_session_sweep/3m",
    8:  "NQ/range_contraction_break/30m",
    9:  "GC/session_momentum_follow/3m",
    10: "GC/trade_absorption_signal/30m",
    11: "ES/avg_order_size_divergence/30m",
    12: "NQ/trade_absorption_signal/30m",
    13: "ES/key_level_cvd_rejection/15m",
    14: "NQ/key_level_cvd_rejection/15m",
    15: "GC/key_level_cvd_rejection/5m",
}


def _load_logs(days: int) -> list[dict]:
    """Load signal JSONL records for the last N calendar days."""
    now   = datetime.now(timezone.utc)
    recs  = []
    found = 0
    for d in range(days):
        date_str = (now - timedelta(days=d)).strftime("%Y%m%d")
        path     = LOG_DIR / f"signals_{date_str}.jsonl"
        if not path.exists():
            continue
        found += 1
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    recs.append(rec)
                except json.JSONDecodeError:
                    continue
    if not recs and days == 1:
        print(f"  No log found for today. Log dir: {LOG_DIR}")
    return recs


def _fmt_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M UTC")
    except Exception:
        return iso[:16]


def _dir_str(signal: int) -> str:
    if signal > 0:
        return "↑ LONG "
    if signal < 0:
        return "↓ SHORT"
    return "  FLAT "


def run(days: int = 1, strat_filter: int | None = None,
        accepted_only: bool = False, recent_n: int = 0,
        output_json: bool = False, trades_only: bool = False):

    recs = _load_logs(days)
    if not recs:
        print(f"\n  No signal logs found for the last {days} day(s).\n")
        return

    # Partition into signal entries and exit events
    exit_recs  = [r for r in recs if r.get("event_type") == "exit"]
    entry_recs = [r for r in recs if r.get("event_type") != "exit"]

    if strat_filter is not None:
        entry_recs = [r for r in entry_recs if r.get("strategy_id") == strat_filter]
        exit_recs  = [r for r in exit_recs  if r.get("strategy_id") == strat_filter]
    if accepted_only:
        entry_recs = [r for r in entry_recs if r.get("accepted")]

    entry_recs.sort(key=lambda r: r.get("timestamp", ""))
    exit_recs.sort(key=lambda r: r.get("timestamp", ""))
    recs = entry_recs  # rest of function uses 'recs' for signal entries only

    # ── Per-strategy stats ────────────────────────────────────────────────────
    by_strat: dict[int, dict] = defaultdict(lambda: {
        "accepted": 0, "rejected": 0, "long": 0, "short": 0,
        "rejection_reasons": defaultdict(int),
        "last_accepted_ts": "",
        "last_signal":     0,
    })
    mode_counts: dict[str, int] = defaultdict(int)

    for r in recs:
        sid    = r.get("strategy_id", 0)
        ok     = r.get("accepted", False)
        sig    = r.get("signal", 0)
        reason = r.get("rejection_reason", "")
        mode   = r.get("mode", "?")
        mode_counts[mode] += 1

        s = by_strat[sid]
        if ok:
            s["accepted"] += 1
            if sig > 0:
                s["long"] += 1
            elif sig < 0:
                s["short"] += 1
            s["last_accepted_ts"] = r.get("timestamp", "")
            s["last_signal"]      = sig
        else:
            s["rejected"] += 1
            if reason:
                s["rejection_reasons"][reason.split(":")[0].split("(")[0].strip()] += 1

    # ── Header ────────────────────────────────────────────────────────────────
    print(f"\n{'═' * 72}")
    print(f"  SIGNAL LOG REPORT — last {days} day(s)")
    if strat_filter is not None:
        print(f"  Filter: strategy {strat_filter} only")
    if accepted_only:
        print(f"  Filter: accepted signals only")

    total_accepted = sum(s["accepted"] for s in by_strat.values())
    total_rejected = sum(s["rejected"] for s in by_strat.values())
    total_signals  = total_accepted + total_rejected
    modes_str = "  ".join(f"{m}:{n}" for m, n in sorted(mode_counts.items()))
    print(f"  Total signals: {total_signals}  |  Accepted: {total_accepted}  "
          f"|  Rejected: {total_rejected}")
    print(f"  Mode breakdown: {modes_str}")
    print(f"{'═' * 72}")

    if not by_strat:
        print("  No signals found.\n")
        return

    # ── Per-strategy table ────────────────────────────────────────────────────
    print(f"\n  {'ID':>3}  {'Strategy':<40}  {'Accept':>7}  {'Reject':>7}  "
          f"{'Acc%':>5}  {'L/S':>5}  Last")
    print(f"  {'─'*3}  {'─'*40}  {'─'*7}  {'─'*7}  {'─'*5}  {'─'*5}  {'─'*16}")

    for sid in sorted(by_strat.keys()):
        s      = by_strat[sid]
        name   = _STRAT_NAMES.get(sid, f"strategy_{sid}")
        acc    = s["accepted"]
        rej    = s["rejected"]
        total  = acc + rej
        pct    = (acc / total * 100) if total else 0
        ls_str = f"{s['long']}L/{s['short']}S"
        last   = _fmt_time(s["last_accepted_ts"]) if s["last_accepted_ts"] else "—"
        print(f"  {sid:>3}  {name:<40}  {acc:>7}  {rej:>7}  "
              f"{pct:>5.0f}%  {ls_str:>5}  {last}")

    # ── Rejection breakdown ───────────────────────────────────────────────────
    all_reasons: dict[str, int] = defaultdict(int)
    for s in by_strat.values():
        for reason, count in s["rejection_reasons"].items():
            all_reasons[reason] += count

    if all_reasons:
        print(f"\n  Rejection reasons (all strategies):")
        for reason, count in sorted(all_reasons.items(), key=lambda x: -x[1]):
            pct = count / total_rejected * 100 if total_rejected else 0
            bar = "█" * int(pct / 5)
            print(f"    {reason:<35}  {count:>5}  ({pct:4.0f}%)  {bar}")

    # ── Recent signals ────────────────────────────────────────────────────────
    n_recent = recent_n if recent_n > 0 else 15
    recent   = [r for r in recs if r.get("signal", 0) != 0][-n_recent:]

    print(f"\n  Recent signals (last {min(len(recent), n_recent)}):")
    print(f"  {'Time':<16}  {'Mode':>8}  {'ID':>3}  {'Symbol':<6}  "
          f"{'Dir':>7}  {'Entry':>8}  {'Stop':>8}  {'Target':>8}  {'OK':>4}  Reason")
    print(f"  {'─'*16}  {'─'*8}  {'─'*3}  {'─'*6}  "
          f"{'─'*7}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*4}  {'─'*20}")

    for r in recent:
        ts     = _fmt_time(r.get("timestamp", ""))
        mode   = r.get("mode", "?")[:8]
        sid    = r.get("strategy_id", 0)
        sym    = r.get("symbol", "?")[:6]
        sig    = r.get("signal", 0)
        entry  = r.get("entry", 0)
        stop   = r.get("stop", 0)
        tgt    = r.get("target", 0)
        ok_str = "✔" if r.get("accepted") else "✖"
        reason = r.get("rejection_reason", "")[:30]
        print(f"  {ts:<16}  {mode:>8}  {sid:>3}  {sym:<6}  "
              f"{_dir_str(sig)}  {entry:>8.2f}  {stop:>8.2f}  {tgt:>8.2f}  "
              f"{ok_str:>4}  {reason}")

    # ── Daily breakdown ───────────────────────────────────────────────────────
    if days > 1:
        daily: dict[str, dict] = defaultdict(lambda: {"accepted": 0, "rejected": 0})
        for r in recs:
            ts = r.get("timestamp", "")[:10]
            if r.get("accepted"):
                daily[ts]["accepted"] += 1
            else:
                daily[ts]["rejected"] += 1

        print(f"\n  Daily breakdown:")
        for date_str in sorted(daily.keys(), reverse=True):
            d   = daily[date_str]
            tot = d["accepted"] + d["rejected"]
            pct = (d["accepted"] / tot * 100) if tot else 0
            bar = "█" * d["accepted"]
            print(f"    {date_str}  accepted={d['accepted']:>4}  rejected={d['rejected']:>4}  "
                  f"acc={pct:.0f}%  {bar}")

    # ── Trade outcomes (exits) ────────────────────────────────────────────────
    full_closes = [e for e in exit_recs
                   if e.get("reason") not in ("ratchet_1", "ratchet_2", "partial_tp")]
    ratchet_events = [e for e in exit_recs
                      if e.get("reason") in ("ratchet_1", "ratchet_2")]

    if exit_recs:
        print(f"\n  Trade Outcomes ({len(full_closes)} closed trades, "
              f"{len(ratchet_events)} ratchet events):")
        if full_closes:
            wins   = [e for e in full_closes if e.get("total_trade_pnl", 0) > 0]
            losses = [e for e in full_closes if e.get("total_trade_pnl", 0) <= 0]
            total_pnl = sum(e.get("total_trade_pnl", 0) for e in full_closes)
            avg_r  = (sum(e.get("r_multiple", 0) for e in full_closes) / len(full_closes)
                      if full_closes else 0)
            print(f"  Win rate: {len(wins)}/{len(full_closes)} "
                  f"({len(wins)/len(full_closes)*100:.0f}%)  "
                  f"Avg R: {avg_r:+.2f}  Total P&L: ${total_pnl:+,.0f}")

            print(f"\n  {'Time':<16}  {'ID':>3}  {'Sym':<6}  {'Dir':>5}  "
                  f"{'Reason':<9}  {'Entry':>8}  {'Exit':>8}  "
                  f"{'R':>6}  {'P&L':>8}  Flags")
            print(f"  {'─'*16}  {'─'*3}  {'─'*6}  {'─'*5}  "
                  f"{'─'*9}  {'─'*8}  {'─'*8}  {'─'*6}  {'─'*8}  {'─'*12}")
            for e in full_closes[-20:]:  # last 20 closed trades
                ts     = _fmt_time(e.get("timestamp", ""))
                sid    = e.get("strategy_id", 0)
                sym    = e.get("symbol", "?")[:6]
                d      = e.get("direction", 0)
                dir_s  = "LONG" if d == 1 else "SHORT"
                rsn    = e.get("reason", "?")[:9]
                entry  = e.get("entry_px", 0)
                exit_p = e.get("exit_px", 0)
                r_mult = e.get("r_multiple", 0)
                pnl    = e.get("total_trade_pnl", 0)
                flags  = []
                if e.get("ratchet_1_done"):
                    flags.append("R1")
                if e.get("ratchet_2_done"):
                    flags.append("R2")
                if e.get("account_halt"):
                    flags.append("HALT")
                flag_s = " ".join(flags)
                pnl_s  = f"${pnl:+,.0f}"
                print(f"  {ts:<16}  {sid:>3}  {sym:<6}  {dir_s:>5}  "
                      f"{rsn:<9}  {entry:>8.2f}  {exit_p:>8.2f}  "
                      f"{r_mult:>+6.2f}R  {pnl_s:>8}  {flag_s}")

    if output_json:
        output = {
            "days":    days,
            "total":   total_signals,
            "accepted": total_accepted,
            "rejected": total_rejected,
            "by_strategy": {sid: dict(s) for sid, s in by_strat.items()},
            "rejection_reasons": dict(all_reasons),
            "closed_trades": len(full_closes),
            "trade_outcomes": [
                {k: e.get(k) for k in (
                    "timestamp", "strategy_id", "symbol", "reason",
                    "direction", "entry_px", "exit_px", "r_multiple",
                    "total_trade_pnl", "bar_count", "ratchet_1_done", "ratchet_2_done",
                )} for e in full_closes
            ],
        }
        print("\n" + json.dumps(output, indent=2, default=str))

    print()


def main():
    parser = argparse.ArgumentParser(description="Signal log analysis")
    parser.add_argument("--days",      type=int, default=1,
                        help="Number of calendar days to include (default: 1)")
    parser.add_argument("--strategy",  type=int, default=None,
                        help="Filter to a single strategy ID")
    parser.add_argument("--accepted",  action="store_true",
                        help="Show only accepted signals")
    parser.add_argument("--recent",    type=int, default=0,
                        help="Number of recent signals to display (default: 15)")
    parser.add_argument("--trades",    action="store_true",
                        help="Focus on closed trade outcomes (exits + R-multiples)")
    parser.add_argument("--json",      action="store_true",
                        help="Output machine-readable JSON summary")
    args = parser.parse_args()

    run(
        days          = args.days,
        strat_filter  = args.strategy,
        accepted_only = args.accepted,
        recent_n      = args.recent,
        output_json   = args.json,
        trades_only   = args.trades,
    )


if __name__ == "__main__":
    main()
