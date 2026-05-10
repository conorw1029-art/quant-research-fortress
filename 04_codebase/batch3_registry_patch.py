# batch3_registry_patch.py - appends Batch 3 entries to registry.py
import sys; sys.path.insert(0,'.')

with open('src/zoo/registry.py','r',encoding='utf-8') as f:
    content = f.read()

if 'BATCH 3' in content:
    print('Registry already contains Batch 3 entries. Skipping.')
    sys.exit(0)

addition = '''

# ----- BATCH 3: TIME-SERIES MOMENTUM (CTA-style) -----
# Baltas-Kosowski (2017), Moskowitz-Ooi-Pedersen (2012)
_STRATEGIES.extend(_multi(
    "tsm", "src.strategies.tsm", "TimeSeriesMomentumStrategy",
    "trend", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "1D", ALL_MKTS, [],
    "Batch3. Time-series momentum. Baltas-Kosowski 2017.",
))

# ----- BATCH 3: BONDARENKO OVERNIGHT DRIFT -----
# Bondarenko-Muravyev (JFQA 2023): 100% of S&P futures return earned
# in 4-hour window around European open. Sharpe 1.6 after costs.
_STRATEGIES.extend(_multi(
    "overnight_drift", "src.strategies.overnight_drift",
    "BondarenkoOvernightDriftStrategy",
    "calendar", Status.EXPERIMENTAL, TestMethod.WALK_FORWARD,
    "5min",
    ["ES", "NQ", "RTY", "YM", "ZN", "ZB"],
    [],
    "Batch3. Bondarenko-Muravyev overnight drift. JFQA 2023.",
))
'''

with open('src/zoo/registry.py','a',encoding='utf-8') as f:
    f.write(addition)

print('Registry patched with Batch 3 entries.')
