"""
patch_registry_reject_dead.py
Marks gap_fill, fib_retracement, ib_fade as REJECTED in registry.py.
These generate 0 signals — running WFO on them causes infinite hangs.

Run from 04_codebase/:
    python patch_registry_reject_dead.py
"""
import sys
sys.path.insert(0, '.')

REGISTRY_PATH = "src/zoo/registry.py"
DEAD_PREFIXES = ("gap_fill_", "fib_retracement_", "ib_fade_")

src = open(REGISTRY_PATH, encoding='utf-8').read()
lines = src.splitlines()

out = []
changed = []
i = 0
while i < len(lines):
    line = lines[i]
    if 'key=' in line and any(f'"{p}' in line or f"'{p}" in line for p in DEAD_PREFIXES):
        block = [line]
        j = i + 1
        while j < len(lines) and j < i + 15:
            bline = lines[j]
            if 'Status.EXPERIMENTAL' in bline:
                key_part = [l for l in block + [bline] if 'key=' in l]
                key = key_part[0].split('key=')[1].split(',')[0].strip().strip('"\'') if key_part else '?'
                bline = bline.replace('Status.EXPERIMENTAL', 'Status.REJECTED')
                changed.append(key)
            block.append(bline)
            j += 1
        out.extend(block)
        i = j
        continue
    out.append(line)
    i += 1

open(REGISTRY_PATH, 'w', encoding='utf-8').write('\n'.join(out))
print(f"Patched {len(changed)} entries to REJECTED:")
for k in changed:
    print(f"  + {k}")

from src.zoo.registry import get_by_status, Status
exp = get_by_status(Status.EXPERIMENTAL)
still_dead = [e.key for e in exp if any(e.key.startswith(p) for p in DEAD_PREFIXES)]
print(f"\nVerification: {len(still_dead)} dead strategies still EXPERIMENTAL (should be 0)")
print(f"Total EXPERIMENTAL remaining: {len(exp)}")
