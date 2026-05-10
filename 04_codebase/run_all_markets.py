import sys, time, subprocess
from pathlib import Path
sys.path.insert(0, '.')
from src.zoo.registry import get_by_status, Status
from src.data.data_schema import DATA_PATHS

raw_dir = Path('..') / '01_data' / 'raw'
available = {k for k, f in DATA_PATHS.items() if (raw_dir / f).exists()}
entries = [e for e in get_by_status(Status.EXPERIMENTAL) if e.data_path_key in available]

print(f"BATCH START: {len(entries)} strategies, {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Data available: {sorted(available)}")
print('=' * 70)

results = []
for i, e in enumerate(entries):
    print(f"\n[{i+1}/{len(entries)}] {e.key} ({e.instrument} on {e.data_path_key})")
    try:
        r = subprocess.run(
            [sys.executable, 'run_strategy.py', '--key', e.key, '--cost-scenario', 'realistic'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd='.',
        )
        verdict, dsr = 'ERROR', 'N/A'
        for line in r.stdout.split('\n'):
            if 'verdict=' in line.lower():
                for p in line.split():
                    if p.startswith('verdict='): verdict = p.split('=')[1]
                    elif p.startswith('DSR='): dsr = p.split('=')[1]
        print(f'  -> {verdict} DSR={dsr}')
        if r.returncode != 0:
            print(f'  STDERR: {r.stderr[:300]}')
        results.append((e.key, verdict, dsr))
    except Exception as ex:
        print(f'  EXCEPTION: {ex}')
        results.append((e.key, 'EXCEPTION', str(ex)))

print('\n' + '=' * 70)
print(f"BATCH COMPLETE: {len(results)} tested")
passes = [r for r in results if r[1] == 'PASS']
fails = [r for r in results if r[1] == 'FAIL']
errors = [r for r in results if r[1] not in ('PASS', 'FAIL')]
print(f"PASS: {len(passes)}  FAIL: {len(fails)}  ERROR: {len(errors)}")
if passes:
    print("\nSURVIVORS:")
    for k, v, d in passes:
        print(f"  {k:<35s} DSR={d}")
if errors:
    print("\nERRORS:")
    for k, v, d in errors:
        print(f"  {k:<35s} {v}")
print(f"\nFinished: {time.strftime('%Y-%m-%d %H:%M:%S')}")