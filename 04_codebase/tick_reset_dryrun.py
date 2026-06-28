#!/usr/bin/env python3
"""
tick_reset_dryrun.py — reset DRY_RUN state to a clean $0 testing baseline.

Backs up all current state, then zeroes positions / P&L / brackets / halts and
creates a `testing_pnl.json` cumulative tracker (strategy P&L from 0, decoupled
from the prop-firm account equity).

Run with the executor STOPPED (the wrapper handles that):
    systemctl stop fortress-executor
    /opt/fortress/venv/bin/python tick_reset_dryrun.py [--equity 49000]
    systemctl start fortress-executor

Dedup keys (processed_signals.json, last_seen_bar.json) are intentionally LEFT
untouched so the executor does not re-emit signals for already-seen bars on restart.
"""
from __future__ import annotations
import argparse, json, shutil
from datetime import datetime, timezone
from pathlib import Path

STATE = Path("/opt/fortress/06_live_trading/state")
LOGS  = Path("/opt/fortress/06_live_trading/logs")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", type=float, default=49000.0,
                    help="Neutral starting equity for risk/drawdown math (display P&L still starts at 0)")
    ap.add_argument("--max-dd", type=float, default=800.0)
    ap.add_argument("--daily-loss-limit", type=float, default=-600.0)
    args = ap.parse_args()

    now   = datetime.now(timezone.utc).isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # 1. Back up everything first (reversible)
    backup = STATE.parent / f"state_backup_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for f in STATE.glob("*.json"):
        shutil.copy2(f, backup / f.name)
    print(f"[backup] state -> {backup}")

    def write(name: str, obj: dict):
        (STATE / name).write_text(json.dumps(obj, indent=2))
        print(f"[reset]  {name}")

    # 2. Clean baselines (schemas match what the executor/state_manager expect)
    write("positions.json", {"last_updated": now, "source": "reset", "positions": {}})
    write("daily_pnl.json", {
        "date": now[:10], "realized_pnl": 0.0, "per_strategy": {},
        "daily_loss_limit": args.daily_loss_limit,
        "daily_loss_remaining": args.daily_loss_limit,
        "halt_triggered": False, "last_updated": now,
    })
    write("account_state.json", {
        "last_updated": now, "account_id": None, "account_halt": False,
        "account_halt_reason": None, "daily_loss_triggered": False,
        "trailing_drawdown_remaining": args.max_dd, "max_drawdown_limit": args.max_dd,
        "session_open": True, "equity": args.equity, "equity_peak": args.equity,
        "realized_pnl": 0.0,
    })
    write("active_brackets.json", {"last_updated": now, "brackets": {}})
    write("open_orders.json", {"last_updated": now, "orders": {}})
    write("strategy_halts.json", {})

    # 3. Cumulative TESTING P&L tracker — the "real" number, starts at 0
    write("testing_pnl.json", {
        "testing_start": now,
        "cumulative_realized_pnl": 0.0,
        "trades": 0, "wins": 0, "losses": 0,
        "per_strategy": {},
        "note": "Strategy P&L since testing reset. Prop-firm account equity decoupled. "
                "Each trade = entry at signal price, exit at SL/TP/ratchet/timeout, "
                "incl. commission (~$6 RT/contract).",
    })

    # 4. Archive old signal logs so the testing window starts clean
    moved = 0
    if LOGS.exists():
        arch = LOGS / f"archive_{stamp}"
        for f in list(LOGS.glob("signals_*.jsonl")):
            arch.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(arch / f.name)); moved += 1
    print(f"[archive] {moved} signal log(s) moved")
    print(f"RESET COMPLETE — baseline $0, no positions, equity ${args.equity:,.0f}. Backup: {backup}")


if __name__ == "__main__":
    main()
