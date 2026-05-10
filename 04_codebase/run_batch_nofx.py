"""
run_batch_nofx.py
=================
Runs all EXPERIMENTAL strategies on non-FX markets using the
original run_strategy.py (which handles feature pre-computation).
Skips 6B, 6E, 6J, 6C, 6A — too slow on 3M+ bar datasets.

Run from 04_codebase/:
    python run_batch_nofx.py
"""
import subprocess, sys, time
from pathlib import Path

sys.path.insert(0, '.')
from src.zoo.registry import get_by_status, Status

SKIP_MARKETS = {'6B', '6E', '6J', '6C', '6A'}
PYTHON = str(Path(sys.executable))

entries = [e for e in get_by_status(Status.EXPERIMENTAL)
           if e.data_path_key not in SKIP_MARKETS]

print(f"BATCH: {len(entries)} strategies (FX skipped)")
print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)

passes, fails, errors = [], [], []
t_start = time.time()

for i, e in enumerate(entries):
    print(f"[{i+1}/{len(entries)}] {e.key}", end="", flush=True)
    t0 = time.time()
    r = subprocess.run(
        [PYTHON, 'run_strategy.py', '--key', e.key, '--cost-scenario', 'realistic'],
        capture_output=True, text=True, cwd='.'
    )
    elapsed = time.time() - t0

    verdict, dsr = 'ERROR', 'N/A'
    for line in r.stdout.split('\n'):
        if 'verdict=' in line.lower():
            for p in line.split():
                if p.startswith('verdict='): verdict = p.split('=')[1]
                if p.startswith('DSR='):     dsr     = p.split('=')[1]

    print(f"  -> {verdict}  DSR={dsr}  ({elapsed:.0f}s)")

    if verdict == 'PASS':   passes.append((e.key, dsr))
    elif verdict == 'FAIL': fails.append(e.key)
    else:                   errors.append(e.key)

total = time.time() - t_start
print(f"\n{'='*70}")
print(f"DONE: {len(entries)} in {total/60:.0f}min")
print(f"PASS={len(passes)}  FAIL={len(fails)}  ERROR={len(errors)}")

if passes:
    print("\nSURVIVORS:")
    for k, d in passes: print(f"  ★ {k}  DSR={d}")
if errors:
    print(f"\nERRORS ({len(errors)}): {errors}")
print(f"\nFinished: {time.strftime('%Y-%m-%d %H:%M:%S')}")
