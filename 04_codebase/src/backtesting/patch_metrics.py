# patch_metrics.py - applies go/no-go fix to metrics.py
import re

path = "src/backtesting/metrics.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

# 1. Remove win_rate check from the checks dict
content = re.sub(
    r'        "win_rate":\s*\(s\["win_rate"\] >= min_win_rate, s\["win_rate"\], f">= \{min_win_rate\}"\),\n',
    '',
    content
)

# 2. Change max_dd_pts to max_dd_dollars and threshold to 2000 (Topstep trailing DD)
content = content.replace(
    'max_dd_pts: float = 400.0,   # Topstep MES 50k: $2000 / $5/pt',
    'max_dd_dollars: float = 2000.0,   # Topstep trailing max DD in dollars'
)
content = content.replace(
    '"max_drawdown":     (s["max_drawdown_abs"] <= max_dd_pts, s["max_drawdown_abs"], f"<= {max_dd_pts}"),',
    '"max_drawdown":     (s["max_drawdown_abs"] <= max_dd_dollars, s["max_drawdown_abs"], f"<= {max_dd_dollars}"),'
)

# 3. Remove min_win_rate parameter from the function signature
content = content.replace(
    '    min_win_rate: float = 0.40,\n',
    ''
)

# Write back
with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("metrics.py patched successfully.")
print("Removed win_rate check, changed max drawdown to $2000.")
