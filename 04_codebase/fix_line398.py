# fix_line398.py – one-time patch for metrics.py
path = "src/backtesting/metrics.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

# line 398 (index 397)
old = lines[397]
indent = old[:len(old) - len(old.lstrip())]
lines[397] = indent + 'return {"mc_pvalue": 1.0, "observed_sharpe": 0.0, "n_permutations": n_permutations}\n'

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("Line 398 patched successfully.")
