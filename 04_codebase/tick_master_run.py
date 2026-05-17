#!/usr/bin/env python3
"""
L2 Tick Master Runner — Complete Unattended Analysis
=====================================================
Runs every combination of bar size x symbol x strategy with full WFO,
then runs portfolio analysis. Set it and walk away.

Schedule:
  Phase 1: 3-min  bars — all 4 symbols, 30 strategies (ETA ~25 min)
  Phase 2: 15-min bars — all 4 symbols, 30 strategies (ETA ~15 min)
  Phase 3: 30-min bars — all 4 symbols, 30 strategies (ETA ~10 min)
  Phase 4: Portfolio analysis — correlation, MC, OHLCV+L2 combined

Note: 1-min and 5-min bars are typically run separately (already done or
      launched in parallel). This script handles the remaining 3 sizes.

Usage:
  python tick_master_run.py
  python tick_master_run.py --all-bars     # include 1-min and 5-min too
  python tick_master_run.py --portfolio-only  # skip strategy runs
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

CODEBASE = Path(__file__).parent

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run_phase(label: str, cmd: list) -> bool:
    t0 = time.time()
    print(f"\n{'='*70}")
    print(f"  PHASE: {label}")
    print(f"  Started: {datetime.now().strftime('%H:%M:%S')}")
    print(f"  Command: {' '.join(cmd)}")
    print(f"{'='*70}\n")

    proc = subprocess.run(cmd, cwd=str(CODEBASE.parent))
    elapsed = (time.time() - t0) / 60

    if proc.returncode == 0:
        print(f"\n  [OK] {label} complete in {elapsed:.1f} min")
        return True
    else:
        print(f"\n  [ERROR] {label} failed (exit {proc.returncode}) after {elapsed:.1f} min")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-bars",      action="store_true",
                        help="Also run 1-min and 5-min (if not already done)")
    parser.add_argument("--portfolio-only", action="store_true",
                        help="Skip strategy runs, go straight to portfolio analysis")
    args = parser.parse_args()

    py = sys.executable
    runner   = str(CODEBASE / "tick_full_run.py")
    analyser = str(CODEBASE / "tick_portfolio_analysis.py")

    total_start = time.time()
    print(f"\n{'='*70}")
    print(f"  L2 TICK MASTER RUN — COMPLETE ANALYSIS")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    phases = []

    if not args.portfolio_only:
        if args.all_bars:
            phases += [
                ("1-min bars — all symbols/strategies",  [py, runner, "--bar-minutes", "1"]),
                ("5-min bars — all symbols/strategies",  [py, runner, "--bar-minutes", "5"]),
            ]

        phases += [
            ("3-min bars  — all symbols/strategies", [py, runner, "--bar-minutes", "3"]),
            ("15-min bars — all symbols/strategies", [py, runner, "--bar-minutes", "15"]),
            ("30-min bars — all symbols/strategies", [py, runner, "--bar-minutes", "30"]),
        ]

    phases += [
        ("Supplemental — 6 new strategies on 1/3/5-min bars",
         [py, str(CODEBASE / "tick_supplement_run.py")]),
        ("Portfolio analysis — correlation, MC, OHLCV+L2",
         [py, analyser]),
    ]

    failed = []
    for label, cmd in phases:
        ok = run_phase(label, cmd)
        if not ok:
            failed.append(label)

    total_min = (time.time() - total_start) / 60
    print(f"\n{'='*70}")
    print(f"  MASTER RUN COMPLETE — {total_min:.1f} min total")
    if failed:
        print(f"  FAILED phases: {failed}")
    else:
        print(f"  All phases succeeded.")
    print(f"  Results in: 05_backtests/")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
