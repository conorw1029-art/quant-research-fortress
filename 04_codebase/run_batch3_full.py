import subprocess, sys, time
sys.path.insert(0, '.')

from src.zoo.registry import get_by_status, Status

def run_batch(entries, label):
    print(f'\n===== {label}: {len(entries)} strategies =====')
    t0 = time.time()
    passes = []
    for i, e in enumerate(entries):
        key = e.key
        print(f'[{i+1}/{len(entries)}] {key}', end='', flush=True)
        try:
            r = subprocess.run(
                [sys.executable, 'run_strategy.py', '--key', key,
                 '--cost-scenario', 'realistic'],
                capture_output=True, text=True, cwd='.', timeout=900
            )
        except subprocess.TimeoutExpired:
            print('  -> TIMEOUT')
            continue
        # The DONE line is in STDERR, not STDOUT
        combined = r.stdout + r.stderr
        v, d = 'ERROR', 'N/A'
        for line in combined.split('\n'):
            if 'DONE: verdict=' in line:
                parts = line.split()
                for p in parts:
                    if p.startswith('verdict='):
                        v = p.split('=')[1]
                    if p.startswith('DSR='):
                        d = p.split('=')[1]
                break
        print(f'  -> {v} DSR={d}')
        if v == 'PASS':
            passes.append((key, d))
    elapsed = time.time() - t0
    print(f'\n{label} DONE in {elapsed/60:.1f}min. SURVIVORS: {passes}')
    return passes

all_exp = get_by_status(Status.EXPERIMENTAL)

# 1) TSM
tsm_entries = [e for e in all_exp if e.key.startswith('tsm_')]
tsm_survivors = run_batch(tsm_entries, 'TSM') if tsm_entries else []

# 2) Overnight drift
od_entries = [e for e in all_exp if e.key.startswith('overnight_drift_')]
od_survivors = run_batch(od_entries, 'Overnight Drift') if od_entries else []

# 3) Zoo re-evaluation
print('\n===== ZOO RE-EVALUATION =====')
r = subprocess.run([sys.executable, 'zoo_reevaluate.py'],
                   capture_output=True, text=True, cwd='.')
print(r.stdout)
if r.stderr:
    print('STDERR:', r.stderr[:500])
