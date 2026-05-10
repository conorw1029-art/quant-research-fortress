import json
from collections import defaultdict
from pathlib import Path

counts = defaultdict(lambda: {'PASS': 0, 'FAIL': 0})
zoo = Path('../05_backtests/zoo.jsonl')

with open(zoo, encoding='utf-8') as f:
    for line in f:
        r = json.loads(line)
        name = r.get('strategy_name', '?')
        v = r.get('verdict', '?')
        if v in ('PASS', 'FAIL'):
            counts[name][v] += 1

print(f"{'Strategy':<35} PASS  FAIL")
print("-" * 50)
for name in sorted(counts):
    p = counts[name]['PASS']
    f = counts[name]['FAIL']
    marker = " ★" if p > 0 else ""
    print(f"{name:<35} {p:4d}  {f:4d}{marker}")

print(f"\nTotal unique strategies tested: {len(counts)}")
print(f"Strategies with any PASS: {sum(1 for v in counts.values() if v['PASS'] > 0)}")
