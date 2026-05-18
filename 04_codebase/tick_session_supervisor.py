"""
tick_session_supervisor.py — Session Supervisor
================================================
Starts and monitors the bar builder + executor as parallel processes.
Automatically restarts either process if it crashes.
Handles clean shutdown on Ctrl+C or KILL_SWITCH.txt.

IMPORTANT: Run tick_startup_checklist.py first to verify the system is ready.

Processes:
  1. tick_bar_builder.py (Gate 3) — connects to Tradovate WebSocket, builds bars
  2. tick_live_executor.py        — reads bars, computes signals, places orders

Usage:
  python tick_session_supervisor.py              # dry-run, no bar builder
  python tick_session_supervisor.py --with-bars  # bar builder + executor (needs creds)
  python tick_session_supervisor.py --demo       # demo auto-trade mode
  python tick_session_supervisor.py --poll 60    # executor poll interval
  python tick_session_supervisor.py --quiet      # suppress hold/flat lines

Exit codes:
  0 — clean shutdown (Ctrl+C or KILL_SWITCH.txt)
  1 — startup failure or max-restart limit reached
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT     = Path(__file__).parent.parent
CODE_DIR = Path(__file__).parent
PYTHON   = sys.executable

KILL_SWITCH_PATH = ROOT / "KILL_SWITCH.txt"

# Maximum times a crashed process is restarted before giving up
MAX_RESTARTS = 10
# Minimum uptime (seconds) before a restart resets the counter
HEALTHY_UPTIME_THRESHOLD = 60


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def _check_kill_switch() -> bool:
    if not KILL_SWITCH_PATH.exists():
        return False
    try:
        for line in KILL_SWITCH_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            return line.upper() == "STOP"
    except Exception:
        pass
    return False


class ManagedProcess:
    """Wraps a subprocess with automatic restart and health tracking."""

    def __init__(self, name: str, cmd: list[str], max_restarts: int = MAX_RESTARTS):
        self.name         = name
        self.cmd          = cmd
        self.max_restarts = max_restarts
        self.proc         = None
        self.restarts     = 0
        self.start_time   = None
        self.total_uptime = 0.0

    def start(self) -> bool:
        try:
            self.proc       = subprocess.Popen(
                self.cmd,
                stdout=sys.stdout,
                stderr=sys.stderr,
                creationflags=0,
            )
            self.start_time = time.time()
            print(f"  [{_now()}] [{self.name}] Started (PID {self.proc.pid})")
            return True
        except Exception as e:
            print(f"  [{_now()}] [{self.name}] START FAILED: {e}")
            return False

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, graceful_secs: float = 5.0):
        if self.proc and self.proc.poll() is None:
            print(f"  [{_now()}] [{self.name}] Stopping (PID {self.proc.pid})...")
            try:
                self.proc.terminate()
                self.proc.wait(timeout=graceful_secs)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None

    def check_and_restart(self) -> bool:
        """
        If the process has died, restart it (up to max_restarts).
        Returns False if max restarts exceeded.
        """
        if self.is_alive():
            return True

        exit_code = self.proc.returncode if self.proc else -1
        uptime    = time.time() - (self.start_time or time.time())
        self.total_uptime += uptime

        if uptime >= HEALTHY_UPTIME_THRESHOLD:
            self.restarts = 0  # Reset counter after healthy run

        self.restarts += 1
        if self.restarts > self.max_restarts:
            print(f"\n  [{_now()}] [{self.name}] MAX RESTARTS ({self.max_restarts}) "
                  f"reached — giving up. Last exit code: {exit_code}")
            return False

        wait = min(2 ** self.restarts, 60)
        print(f"\n  [{_now()}] [{self.name}] Exited (code={exit_code}, "
              f"uptime={uptime:.0f}s). Restart {self.restarts}/{self.max_restarts} "
              f"in {wait}s...")
        time.sleep(wait)
        return self.start()


def run_supervisor(args):
    processes: list[ManagedProcess] = []

    # ── Build executor command ────────────────────────────────────────────────
    executor_cmd = [
        PYTHON, str(CODE_DIR / "tick_live_executor.py"),
        "--poll", str(args.poll),
    ]
    if args.quiet:
        executor_cmd.append("--quiet")
    if args.demo:
        executor_cmd.append("--demo-auto-trade")
        if args.username:
            executor_cmd += ["--username", args.username]
        if args.password:
            executor_cmd += ["--password", args.password]
        if args.cid:
            executor_cmd += ["--cid", str(args.cid)]
        if args.secret:
            executor_cmd += ["--secret", args.secret]
    if args.alert_file:
        executor_cmd += ["--alert-file", args.alert_file]
    if args.close_weekend:
        executor_cmd.append("--close-weekend")

    executor = ManagedProcess("executor", executor_cmd)
    processes.append(executor)

    # ── Build bar builder command (optional) ──────────────────────────────────
    bar_builder = None
    if args.with_bars:
        bb_cmd = [
            PYTHON, str(CODE_DIR / "tick_bar_builder.py"),
            "--symbols", "GC", "ES", "NQ",
            "--bar-sizes", "1", "3", "5", "15", "30",
        ]
        if args.live:
            bb_cmd.append("--live")
        if args.username:
            bb_cmd += ["--username", args.username]
        if args.password:
            bb_cmd += ["--password", args.password]
        if args.cid:
            bb_cmd += ["--cid", str(args.cid)]
        if args.secret:
            bb_cmd += ["--secret", args.secret]
        bar_builder = ManagedProcess("bar_builder", bb_cmd)
        processes.append(bar_builder)

    # ── Header ────────────────────────────────────────────────────────────────
    mode = "DEMO" if args.demo else "DRY_RUN"
    print(f"\n{'═' * 62}")
    print(f"  FORTRESS SESSION SUPERVISOR")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  Mode:        {mode}")
    print(f"  Executor:    poll={args.poll}s  quiet={args.quiet}")
    print(f"  Bar builder: {'enabled (WebSocket)' if args.with_bars else 'disabled (using static bars)'}")
    print(f"  Max restarts: {MAX_RESTARTS} per process")
    print(f"  Kill switch:  {KILL_SWITCH_PATH}")
    print(f"{'═' * 62}")

    # ── Start processes ───────────────────────────────────────────────────────
    if bar_builder:
        print(f"\n  Starting bar builder...")
        if not bar_builder.start():
            print("  *** Bar builder failed to start — aborting ***")
            return 1
        time.sleep(3)  # Let bar builder authenticate before executor starts

    print(f"\n  Starting executor...")
    if not executor.start():
        print("  *** Executor failed to start — aborting ***")
        bar_builder.stop() if bar_builder else None
        return 1

    print(f"\n  Supervisor running. Ctrl+C or set KILL_SWITCH.txt=STOP to stop.\n")

    # ── Monitor loop ──────────────────────────────────────────────────────────
    try:
        while True:
            time.sleep(10)

            # Kill switch check
            if _check_kill_switch():
                print(f"\n  [{_now()}] KILL SWITCH activated — stopping all processes")
                break

            # Health check each process
            for proc in processes:
                if not proc.check_and_restart():
                    # Max restarts exceeded
                    print(f"  [{_now()}] {proc.name} cannot recover — stopping supervisor")
                    break
            else:
                continue
            break  # inner break propagated here

    except KeyboardInterrupt:
        print(f"\n  [{_now()}] Ctrl+C — shutting down...")

    # ── Shutdown ──────────────────────────────────────────────────────────────
    print(f"\n  [{_now()}] Stopping all processes...")
    for proc in reversed(processes):
        proc.stop()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'─' * 62}")
    print(f"  Session summary:")
    for proc in processes:
        uptime = proc.total_uptime + (time.time() - (proc.start_time or time.time()) if not proc.is_alive() else 0)
        print(f"    {proc.name:<15}  restarts={proc.restarts}  total_uptime~{uptime:.0f}s")
    print(f"{'─' * 62}")
    print(f"  Signal log: {ROOT / '06_live_trading' / 'logs'}")
    print(f"  Run tick_signal_log_reader.py for a session summary.")
    print()

    return 0


def main():
    parser = argparse.ArgumentParser(description="Fortress session supervisor")

    parser.add_argument("--poll",   type=int, default=60,
                        help="Executor poll interval in seconds (default: 60)")
    parser.add_argument("--quiet",  action="store_true",
                        help="Suppress executor hold/flat lines")

    parser.add_argument("--demo",   action="store_true",
                        help="Enable demo auto-trade mode (requires credentials)")
    parser.add_argument("--live",   action="store_true",
                        help="Use live account for bar builder (NOT paper)")

    parser.add_argument("--with-bars", action="store_true",
                        help="Also run tick_bar_builder.py (requires Tradovate credentials)")

    parser.add_argument("--username", default=os.environ.get("TRADOVATE_USERNAME", ""))
    parser.add_argument("--password", default=os.environ.get("TRADOVATE_PASSWORD", ""))
    parser.add_argument("--cid",      type=int,
                        default=int(os.environ.get("TRADOVATE_CID", "0")))
    parser.add_argument("--secret",   default=os.environ.get("TRADOVATE_SECRET", ""))

    parser.add_argument("--alert-file",    type=str, default=None,
                        help="JSON file to append executor alerts to")
    parser.add_argument("--close-weekend", action="store_true",
                        help="Auto-flatten positions Friday 21:45 UTC")

    args = parser.parse_args()

    # Guard: demo mode needs credentials
    if args.demo and not (args.username and args.password):
        print("ERROR: --demo requires credentials (--username, --password, --cid, --secret)")
        print("  Or set TRADOVATE_USERNAME / TRADOVATE_PASSWORD / TRADOVATE_CID / TRADOVATE_SECRET")
        sys.exit(1)

    if args.with_bars and not (args.username and args.password):
        print("ERROR: --with-bars requires credentials (--username, --password, --cid, --secret)")
        sys.exit(1)

    sys.exit(run_supervisor(args))


if __name__ == "__main__":
    main()
