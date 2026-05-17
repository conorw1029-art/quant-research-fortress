#!/usr/bin/env python3
"""
Supplemental runner — 6 new strategies on completed bar sizes.
Runs after the main master run has finished.
Tests: vpin_approximation, stacked_imbalance, vwap_cvd_divergence,
       buying_climax, level_delta_flip, composite_order_flow_score
on 1-min, 3-min, 5-min bars (15-min and 30-min already included in master run).
"""
import subprocess, sys, time
from datetime import datetime
from pathlib import Path

CODEBASE = Path(__file__).parent

NEW_STRATEGIES = [
    "vpin_approximation",
    "stacked_imbalance",
    "vwap_cvd_divergence",
    "buying_climax",
    "level_delta_flip",
    "composite_order_flow_score",
]
BAR_SIZES = [1, 3, 5]  # 15 and 30 already covered by master run

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

py     = sys.executable
runner = str(CODEBASE / "tick_full_run.py")

print(f"\n{'='*65}")
print(f"  SUPPLEMENTAL RUN — 6 New Strategies")
print(f"  Bar sizes: {BAR_SIZES}-min")
print(f"  Strategies: {NEW_STRATEGIES}")
print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"{'='*65}\n")

t0 = time.time()
for bar_min in BAR_SIZES:
    print(f"\n--- {bar_min}-minute bars ---")
    cmd = [py, runner,
           "--bar-minutes", str(bar_min),
           "--strategy-list"] + NEW_STRATEGIES
    proc = subprocess.run(cmd, cwd=str(CODEBASE.parent))
    elapsed = (time.time() - t0) / 60
    status = "OK" if proc.returncode == 0 else f"ERROR({proc.returncode})"
    print(f"  [{status}] {bar_min}-min done  ({elapsed:.1f} min total)")

# Final portfolio analysis across all results
print(f"\n--- Final portfolio analysis ---")
analyser = str(CODEBASE / "tick_portfolio_analysis.py")
subprocess.run([py, analyser], cwd=str(CODEBASE.parent))

total_min = (time.time() - t0) / 60
print(f"\n{'='*65}")
print(f"  Supplemental run complete in {total_min:.1f} min")
print(f"{'='*65}\n")
