"""
Run from 04_codebase/:
  python fix_registry.py
"""
import sys, importlib
sys.path.insert(0, '.')

DEAD = ['gap_fill', 'fib_retracement', 'ib_fade']
PATH = 'src/zoo/registry.py'

lines = open(PATH, encoding='utf-8').read().splitlines()
out = []
patched = 0

for i, line in enumerate(lines):
    if 'Status.EXPERIMENTAL' in line:
        context = ' '.join(lines[max(0, i-3):i+1])
        if any(f'"{d}"' in context for d in DEAD):
            line = line.replace('Status.EXPERIMENTAL', 'Status.REJECTED')
            patched += 1
            print(f'  Patched line {i+1}')
    out.append(line)

open(PATH, 'w', encoding='utf-8').write('\n'.join(out))
print(f'\nPatched {patched} lines.')

# Verify
import src.zoo.registry as reg
importlib.reload(reg)
exp = reg.get_by_status(reg.Status.EXPERIMENTAL)
dead_still = [e.key for e in exp if any(e.key.startswith(d + '_') for d in DEAD)]
print(f'Dead still EXPERIMENTAL: {len(dead_still)} (should be 0)')
print(f'Total EXPERIMENTAL remaining: {len(exp)}')
